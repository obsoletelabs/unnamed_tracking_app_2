"""Contract tests for the source observation registry (#94)."""

import asyncio
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, update

from src.database.models.game import Game
from src.database.models.game_identity import (
    GameIdentity,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
)
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_ingestion.contract import (
    ObservationBatchV1,
    SourceConnectionRef,
    SourceObservationV1,
)
from src.features.game_ingestion.service import (
    complete_inventory,
    disconnect_connection,
    ingest_batch,
    relink_observation,
)

STEAM = SourceConnectionRef(source_kind="host", source="steam", source_scope="legacy")


def batch(*items, connection=STEAM):
    return ObservationBatchV1(
        connection=connection, observations=[SourceObservationV1(**item) for item in items]
    )


@pytest.fixture
async def users():
    created = []

    async def make():
        user_id = uuid4()
        async with SessionLocal() as db:
            await db.execute(
                insert(User).values(
                    id=user_id, username=user_id.hex, email=f"{user_id}@x.test", password_hash="x"
                )
            )
            await db.commit()
        created.append(user_id)
        return user_id

    yield make
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(created)))
        await db.commit()


async def make_game(user_id, title="Hades", deleted_at=None):
    game_id = uuid4()
    async with SessionLocal() as db:
        await db.execute(
            insert(Game).values(
                id=game_id,
                user_id=user_id,
                title=title,
                sort_title=title.lower(),
                folder_location=game_id.hex,
                deleted_at=deleted_at,
            )
        )
        await db.commit()
    return game_id


async def ingest(user_id, *items, attach=None, connection=STEAM, now=None):
    async with SessionLocal() as db:
        result = await ingest_batch(
            db, user_id, batch(*items, connection=connection), attach=attach, now=now
        )
        await db.commit()
        return result


async def counts(user_id):
    async with SessionLocal() as db:
        return tuple(
            [
                await db.scalar(
                    select(func.count()).select_from(model).where(model.user_id == user_id)
                )
                for model in (
                    GameSourceConnection,
                    GameSourceObservation,
                    GameSourceObservationRevision,
                    GameSourceObservationLink,
                    GameIdentity,
                )
            ]
        )


async def observation(user_id, external_id="10", scope="legacy"):
    async with SessionLocal() as db:
        return await db.scalar(
            select(GameSourceObservation)
            .join(
                GameSourceConnection, GameSourceConnection.id == GameSourceObservation.connection_id
            )
            .where(
                GameSourceObservation.user_id == user_id,
                GameSourceObservation.external_id == external_id,
                GameSourceConnection.source_scope == scope,
            )
        )


async def test_replaying_a_payload_creates_nothing_new(users):
    user = await users()
    game = await make_game(user)
    item = {"external_id": "10", "title": "Hades", "facts": {"playtime_seconds": 60}}
    first = await ingest(user, item, attach={"10": game}, now=1000)
    before = await counts(user)
    second = await ingest(user, item, attach={"10": game}, now=2000)
    assert [r.outcome for r in first.results] == ["created"]
    assert [r.outcome for r in second.results] == ["unchanged"]
    assert await counts(user) == before == (1, 1, 1, 1, 0)
    row = await observation(user)
    assert row.revision == 1 and row.last_observed_at == 1000 and row.payload_digest


async def test_changed_payload_updates_only_what_was_observed(users):
    user = await users()
    await ingest(
        user,
        {
            "external_id": "10",
            "title": "Hades",
            "platform": "PC",
            "provider_ids": {"steam": "10"},
            "facts": {"playtime_seconds": 60, "tags": ["rogue"], "owned": True},
        },
        now=1000,
    )
    result = await ingest(user, {"external_id": "10", "facts": {"playtime_seconds": 120}}, now=2000)
    assert result.results[0].changed == ("facts",)
    row = await observation(user)
    assert (row.observed_title, row.observed_platform) == ("Hades", "PC")
    assert row.observed_provider_ids == {"steam": "10"}
    assert row.observed_facts == {"playtime_seconds": 120, "tags": ["rogue"], "owned": True}
    assert row.field_observed_at["playtime_seconds"] == 2000
    assert row.field_observed_at["title"] == 1000 and row.revision == 2
    async with SessionLocal() as db:
        revisions = (
            await db.scalars(
                select(GameSourceObservationRevision)
                .where(GameSourceObservationRevision.observation_id == row.id)
                .order_by(GameSourceObservationRevision.revision)
            )
        ).all()
    assert revisions[1].changes == {"facts": {"playtime_seconds": 120}}


async def test_omitted_and_null_fields_are_unknown_not_a_reset(users):
    user = await users()
    await ingest(user, {"external_id": "10", "title": "Hades", "release_date": "2020-09-17"})
    result = await ingest(
        user, {"external_id": "10", "title": None, "release_date": None, "facts": {}}
    )
    assert result.results[0].outcome == "unchanged"
    row = await observation(user)
    assert row.observed_title == "Hades" and str(row.observed_release_date) == "2020-09-17"


async def test_a_renamed_record_is_the_same_observation(users):
    user = await users()
    game = await make_game(user)
    first = await ingest(user, {"external_id": "10", "title": "Hades"}, attach={"10": game})
    second = await ingest(user, {"external_id": "10", "title": "Hades (2020)"}, attach={"10": game})
    assert first.results[0].observation_id == second.results[0].observation_id
    assert second.results[0].outcome == "updated"
    assert (await counts(user))[:2] == (1, 1)
    assert (await observation(user)).observed_title == "Hades (2020)"


async def test_a_late_retry_cannot_overwrite_newer_source_state(users):
    user = await users()
    await ingest(user, {"external_id": "10", "title": "New", "source_updated_at": 200})
    result = await ingest(user, {"external_id": "10", "title": "Old", "source_updated_at": 100})
    assert result.results[0].outcome == "stale"
    assert (await observation(user)).observed_title == "New"


async def test_a_failed_batch_leaves_nothing_and_its_retry_applies_once(users):
    user = await users()
    async with SessionLocal() as db:
        await ingest_batch(db, user, batch({"external_id": "10", "title": "Hades"}))
        await db.rollback()
    assert await counts(user) == (0, 0, 0, 0, 0)
    await ingest(user, {"external_id": "10", "title": "Hades"})
    await ingest(user, {"external_id": "10", "title": "Hades"})
    assert await counts(user) == (1, 1, 1, 0, 0)


async def test_concurrent_identical_deliveries_make_one_observation(users):
    user = await users()
    item = {"external_id": "10", "title": "Hades", "facts": {"playtime_seconds": 5}}
    await asyncio.gather(*(ingest(user, item) for _ in range(4)))
    assert await counts(user) == (1, 1, 1, 0, 0)


async def test_users_and_scopes_never_collide(users):
    alice, bob = await users(), await users()
    other_scope = SourceConnectionRef(source_kind="host", source="steam", source_scope="acct-2")
    await ingest(alice, {"external_id": "10", "title": "Alice"})
    await ingest(bob, {"external_id": "10", "title": "Bob"})
    await ingest(alice, {"external_id": "10", "title": "Alice 2"}, connection=other_scope)
    assert (await observation(alice)).observed_title == "Alice"
    assert (await observation(bob)).observed_title == "Bob"
    assert (await observation(alice, scope="acct-2")).observed_title == "Alice 2"
    assert (await counts(alice))[:2] == (2, 2) and (await counts(bob))[:2] == (1, 1)


async def test_a_record_cannot_attach_to_another_users_game(users):
    alice, bob = await users(), await users()
    alices_game = await make_game(alice)
    async with SessionLocal() as db:
        with pytest.raises(ValueError):
            await ingest_batch(
                db, bob, batch({"external_id": "10", "title": "X"}), attach={"10": alices_game}
            )


async def test_partial_inventories_never_remove_and_complete_ones_do(users):
    user = await users()
    await ingest(
        user,
        {"external_id": "1", "title": "A"},
        {"external_id": "2", "title": "B"},
        {"external_id": "3", "title": "C"},
    )
    await ingest(user, {"external_id": "1", "title": "A"})  # partial: 2 and 3 missing
    assert (await observation(user, "2")).availability == "available"
    async with SessionLocal() as db:
        outcome = await complete_inventory(db, user, STEAM, {"1", "2"}, now=5000)
        await db.commit()
    assert outcome.marked_unavailable == 1
    gone = await observation(user, "3")
    assert (gone.availability, gone.unavailable_since, gone.observed_title) == (
        "unavailable",
        5000,
        "C",
    )
    again = await ingest(user, {"external_id": "3", "available": True})
    assert again.results[0].outcome == "updated"
    assert (await observation(user, "3")).availability == "available"
    async with SessionLocal() as db:
        connection = await db.scalar(
            select(GameSourceConnection).where(GameSourceConnection.user_id == user)
        )
    assert connection.last_complete_inventory_at == 5000


async def test_an_explicit_unavailable_report_is_recorded_once(users):
    user = await users()
    await ingest(user, {"external_id": "1", "title": "A"})
    first = await ingest(user, {"external_id": "1", "available": False}, now=10)
    second = await ingest(user, {"external_id": "1", "available": False}, now=20)
    assert (first.results[0].outcome, second.results[0].outcome) == ("updated", "unchanged")
    assert (await observation(user, "1")).unavailable_since == 10


async def test_disconnect_keeps_history_and_reconnect_reuses_the_records(users):
    user = await users()
    game = await make_game(user)
    first = await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": game})
    async with SessionLocal() as db:
        assert await disconnect_connection(db, user, STEAM, now=50)
        assert not await disconnect_connection(db, user, STEAM, now=60)
        # an inventory from a disconnected account is not acted on
        assert not (await complete_inventory(db, user, STEAM, set())).applied
        await db.commit()
    before = await counts(user)
    row = await observation(user, "1")
    assert row.availability == "available"
    result = await ingest(user, {"external_id": "1", "title": "A"}, now=70)
    assert result.reconnected and result.connection_id == first.connection_id
    assert result.results[0].observation_id == first.results[0].observation_id
    assert await counts(user) == before
    async with SessionLocal() as db:
        connection = await db.get(GameSourceConnection, result.connection_id)
    assert connection.disconnected_at is None


async def test_moving_a_record_keeps_its_external_id_and_ingest_cannot_undo_it(users):
    user = await users()
    first, second = await make_game(user), await make_game(user, "Hades II")
    result = await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": first})
    observation_id = result.results[0].observation_id
    async with SessionLocal() as db:
        with pytest.raises(ValueError):
            await relink_observation(db, user, observation_id, second, origin="ingest")
        link = await relink_observation(db, user, observation_id, second, origin="user")
        assert link is not None
        assert await relink_observation(db, user, observation_id, second, origin="user") is None
        await db.commit()
    await ingest(user, {"external_id": "1", "title": "A2"}, attach={"1": first})
    async with SessionLocal() as db:
        links = (
            await db.scalars(
                select(GameSourceObservationLink)
                .where(GameSourceObservationLink.observation_id == observation_id)
                .order_by(GameSourceObservationLink.linked_at, GameSourceObservationLink.origin)
            )
        ).all()
    assert len(links) == 2
    assert [link.game_id for link in links if link.unlinked_at is None] == [second]
    assert (await observation(user, "1")).external_id == "1"


async def test_an_unlinked_record_is_not_silently_relinked_by_ingest(users):
    user = await users()
    game = await make_game(user)
    await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": game})
    async with SessionLocal() as db:
        await db.execute(
            update(GameSourceObservationLink).values(
                unlinked_at=GameSourceObservationLink.linked_at
            )
        )
        await db.commit()
    await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": game})
    await ingest(user, {"external_id": "1", "title": "B"}, attach={"1": game})
    async with SessionLocal() as db:
        open_links = await db.scalar(
            select(func.count())
            .select_from(GameSourceObservationLink)
            .where(
                GameSourceObservationLink.user_id == user,
                GameSourceObservationLink.unlinked_at.is_(None),
            )
        )
    assert open_links == 0


async def test_a_link_to_a_trashed_game_follows_the_game_the_adapter_resolved(users):
    user = await users()
    old = await make_game(user)
    await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": old})
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == old).values(deleted_at=int(time.time())))
        await db.commit()
    new = await make_game(user)
    await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": new})
    async with SessionLocal() as db:
        links = (
            await db.scalars(
                select(GameSourceObservationLink).where(GameSourceObservationLink.user_id == user)
            )
        ).all()
    assert sorted(bool(link.unlinked_at) for link in links) == [False, True]
    assert [link.game_id for link in links if link.unlinked_at is None] == [new]
    assert (await counts(user))[1] == 1


async def test_ingestion_never_creates_identities_or_decisions(users):
    user = await users()
    game = await make_game(user)
    await ingest(user, {"external_id": "1", "title": "A"}, {"external_id": "2", "title": "A"})
    await ingest(user, {"external_id": "3", "title": "A"}, attach={"3": game})
    assert (await counts(user))[4] == 0


def test_contract_rejects_bad_payloads():
    with pytest.raises(ValidationError):
        SourceObservationV1(external_id="  ")
    with pytest.raises(ValidationError):
        SourceObservationV1(external_id="1", surprise=True)
    with pytest.raises(ValidationError):
        SourceObservationV1(external_id="1", facts={"playtime_seconds": -1})
    with pytest.raises(ValidationError):
        SourceConnectionRef(source_kind="manual", source="steam", source_scope="local")
    with pytest.raises(ValidationError):
        ObservationBatchV1(
            contract_version=2, connection=STEAM, observations=[{"external_id": "1"}]
        )
    with pytest.raises(ValidationError):
        batch({"external_id": "1"}, {"external_id": "1"})
