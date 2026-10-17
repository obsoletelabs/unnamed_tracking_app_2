"""Schema-level tests for the canonical game identity model (#93).

They exercise the database rules directly: creation, ownership, uniqueness,
relationship integrity, archive/disconnect semantics, and user isolation.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError

from src.database.models.game import Game
from src.database.models.game_identity import (
    GameIdentity,
    GameIdentityDecision,
    GameRelationship,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
    GameUmbrella,
)
from src.database.models.game_identity_tracking import (
    GameFieldValue,
    GameManualAchievement,
    GameMetricAdjustment,
    GameMetricCounter,
    GameMetricCounterMember,
)
from src.database.models.user import User
from src.database.session import SessionLocal


class World:
    """Two users, each with games, an umbrella and a Playnite connection."""

    def __init__(self, db):
        self.db = db
        self.users = []

    async def user(self):
        user_id = uuid4()
        await self.db.execute(
            insert(User).values(
                id=user_id,
                username=user_id.hex,
                email=f"{user_id}@example.test",
                password_hash="x",
            )
        )
        self.users.append(user_id)
        return user_id

    async def game(self, user_id, title="Minecraft", **fields):
        game_id = uuid4()
        await self.db.execute(
            insert(Game).values(
                id=game_id,
                user_id=user_id,
                title=title,
                sort_title=title.lower(),
                folder_location=game_id.hex,
                **fields,
            )
        )
        return game_id

    async def umbrella(self, user_id, title="Minecraft", kind="game"):
        umbrella_id = uuid4()
        await self.db.execute(
            insert(GameUmbrella).values(
                id=umbrella_id, user_id=user_id, kind=kind, title=title, sort_title=title.lower()
            )
        )
        return umbrella_id

    async def connection(self, user_id, source="playnite", scope="legacy", kind="host"):
        connection_id = uuid4()
        await self.db.execute(
            insert(GameSourceConnection).values(
                id=connection_id,
                user_id=user_id,
                source_kind=kind,
                source=source,
                source_scope=scope,
            )
        )
        return connection_id

    async def observation(self, user_id, connection_id, external_id="guid-1", **fields):
        observation_id = uuid4()
        await self.db.execute(
            insert(GameSourceObservation).values(
                id=observation_id,
                user_id=user_id,
                connection_id=connection_id,
                external_id=external_id,
                **fields,
            )
        )
        return observation_id

    async def counter(self, user_id, game_id, metric="playtime"):
        counter_id = uuid4()
        await self.db.execute(
            insert(GameMetricCounter).values(
                id=counter_id, user_id=user_id, game_id=game_id, metric=metric
            )
        )
        return counter_id

    async def cleanup(self):
        await self.db.rollback()
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(self.users)))
            await db.commit()


@pytest.fixture
async def world():
    async with SessionLocal() as db:
        value = World(db)
        yield value
        await value.cleanup()


async def rejected(world, statement):
    """Run a statement that the database must refuse, leaving the session usable."""
    with pytest.raises(IntegrityError):
        async with world.db.begin_nested():
            await world.db.execute(statement)
            # deferred constraints are checked at commit; force them here
            await world.db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


# --- creation and ownership --------------------------------------------------


async def test_minecraft_java_and_bedrock_share_one_umbrella_with_their_own_identities(world):
    user = await world.user()
    java, bedrock = await world.game(user, "Minecraft Java"), await world.game(user, "Minecraft")
    umbrella = await world.umbrella(user)
    await world.db.execute(
        insert(GameIdentity),
        [
            {
                "game_id": java,
                "user_id": user,
                "umbrella_id": umbrella,
                "kind": "edition",
                "label": "Java Edition",
                "is_primary": True,
            },
            {
                "game_id": bedrock,
                "user_id": user,
                "umbrella_id": umbrella,
                "kind": "edition",
                "label": "Bedrock Edition",
                "presentation": "separate",
            },
        ],
    )
    rows = (
        await world.db.execute(
            select(GameIdentity.label, GameIdentity.presentation).order_by(GameIdentity.label)
        )
    ).all()
    assert rows == [("Bedrock Edition", "separate"), ("Java Edition", "grouped")]


async def test_one_primary_identity_per_umbrella(world):
    user = await world.user()
    umbrella = await world.umbrella(user)
    first, second = await world.game(user), await world.game(user)
    await world.db.execute(
        insert(GameIdentity).values(
            game_id=first, user_id=user, umbrella_id=umbrella, is_primary=True
        )
    )
    await rejected(
        world,
        insert(GameIdentity).values(
            game_id=second, user_id=user, umbrella_id=umbrella, is_primary=True
        ),
    )


async def test_an_identity_belongs_to_one_umbrella_and_only_game_umbrellas_hold_identities(world):
    user = await world.user()
    game = await world.game(user)
    series = await world.umbrella(user, "Series", kind="series")
    await rejected(
        world, insert(GameIdentity).values(game_id=game, user_id=user, umbrella_id=series)
    )
    first, second = await world.umbrella(user), await world.umbrella(user, "Other")
    await world.db.execute(
        insert(GameIdentity).values(game_id=game, user_id=user, umbrella_id=first)
    )
    await rejected(
        world, insert(GameIdentity).values(game_id=game, user_id=user, umbrella_id=second)
    )


async def test_unknown_vocabulary_is_rejected(world):
    user = await world.user()
    await rejected(
        world,
        insert(GameUmbrella).values(
            id=uuid4(), user_id=user, kind="bogus", title="x", sort_title="x"
        ),
    )
    await rejected(
        world,
        insert(GameUmbrella).values(id=uuid4(), user_id=user, title="  ", sort_title="x"),
    )
    await rejected(
        world,
        insert(GameSourceConnection).values(
            id=uuid4(), user_id=user, source_kind="bogus", source="x", source_scope="y"
        ),
    )


# --- tenancy -----------------------------------------------------------------


async def test_rows_cannot_reference_another_users_game_umbrella_or_observation(world):
    alice, bob = await world.user(), await world.user()
    alice_game, bob_game = await world.game(alice), await world.game(bob)
    alice_umbrella, bob_umbrella = await world.umbrella(alice), await world.umbrella(bob)
    bob_connection = await world.connection(bob)
    bob_observation = await world.observation(bob, bob_connection)

    await rejected(
        world,
        insert(GameIdentity).values(game_id=bob_game, user_id=alice, umbrella_id=alice_umbrella),
    )
    await rejected(
        world,
        insert(GameIdentity).values(game_id=alice_game, user_id=alice, umbrella_id=bob_umbrella),
    )
    await rejected(
        world,
        insert(GameSourceObservation).values(
            id=uuid4(), user_id=alice, connection_id=bob_connection, external_id="x"
        ),
    )
    await rejected(
        world,
        insert(GameSourceObservationLink).values(
            id=uuid4(), user_id=alice, observation_id=bob_observation, game_id=alice_game
        ),
    )
    await rejected(
        world,
        insert(GameSourceObservationLink).values(
            id=uuid4(),
            user_id=alice,
            observation_id=bob_observation,
            game_id=bob_game,
        ),
    )
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(),
            user_id=alice,
            kind="related_to",
            from_game_id=alice_game,
            to_game_id=bob_game,
        ),
    )
    await rejected(
        world,
        insert(GameManualAchievement).values(
            id=uuid4(), user_id=alice, game_id=bob_game, title="x"
        ),
    )
    await rejected(
        world,
        insert(GameMetricCounter).values(
            id=uuid4(), user_id=alice, game_id=bob_game, metric="playtime"
        ),
    )
    await rejected(
        world,
        insert(GameFieldValue).values(
            id=uuid4(),
            user_id=alice,
            game_id=bob_game,
            field="title",
            origin="user",
        ),
    )
    await rejected(
        world,
        insert(GameIdentityDecision).values(
            id=uuid4(),
            user_id=alice,
            subject_kind="identity_pair",
            first_game_id=min(alice_game, bob_game),
            second_game_id=max(alice_game, bob_game),
            outcome="confirmed_separate",
            actor_kind="user",
        ),
    )


async def test_decision_pointer_must_belong_to_the_same_user(world):
    alice, bob = await world.user(), await world.user()
    alice_game = await world.game(alice)
    bob_a, bob_b = await world.game(bob), await world.game(bob)
    bob_decision = uuid4()
    await world.db.execute(
        insert(GameIdentityDecision).values(
            id=bob_decision,
            user_id=bob,
            subject_kind="identity_pair",
            first_game_id=min(bob_a, bob_b),
            second_game_id=max(bob_a, bob_b),
            outcome="confirmed_same",
            actor_kind="user",
        )
    )
    connection = await world.connection(alice)
    observation = await world.observation(alice, connection)
    await rejected(
        world,
        insert(GameSourceObservationLink).values(
            id=uuid4(),
            user_id=alice,
            observation_id=observation,
            game_id=alice_game,
            decision_id=bob_decision,
        ),
    )


async def test_two_users_may_hold_the_same_source_identity(world):
    alice, bob = await world.user(), await world.user()
    for user in (alice, bob):
        connection = await world.connection(user, "steam", "legacy")
        await world.observation(user, connection, "570")
    count = await world.db.scalar(
        select(func.count())
        .select_from(GameSourceObservation)
        .where(GameSourceObservation.user_id.in_([alice, bob]))
    )
    assert count == 2


# --- source identity and idempotency ----------------------------------------


async def test_source_identity_is_unique_per_connection_and_external_id(world):
    user = await world.user()
    connection = await world.connection(user)
    await world.observation(user, connection, "guid-1")
    await rejected(
        world,
        insert(GameSourceObservation).values(
            id=uuid4(), user_id=user, connection_id=connection, external_id="guid-1"
        ),
    )
    # the same external ID in another account/library scope is a different record
    other_scope = await world.connection(user, "playnite", "library-b")
    await world.observation(user, other_scope, "guid-1")
    await rejected(
        world,
        insert(GameSourceConnection).values(
            id=uuid4(), user_id=user, source_kind="host", source="playnite", source_scope="legacy"
        ),
    )


async def test_manual_connections_use_the_manual_source(world):
    user = await world.user()
    await rejected(
        world,
        insert(GameSourceConnection).values(
            id=uuid4(), user_id=user, source_kind="manual", source="steam", source_scope="local"
        ),
    )
    await world.connection(user, "manual", "local", kind="manual")


async def test_replayed_changes_update_one_observation_and_append_revisions(world):
    user = await world.user()
    connection = await world.connection(user)
    observation = await world.observation(
        user, connection, observed_title="Minecraft", observed_facts={"playtime_seconds": 720000}
    )
    await world.db.execute(
        insert(GameSourceObservationRevision).values(
            id=uuid4(), user_id=user, observation_id=observation, revision=1, changes={}
        )
    )
    await rejected(
        world,
        insert(GameSourceObservationRevision).values(
            id=uuid4(), user_id=user, observation_id=observation, revision=1, changes={}
        ),
    )
    await world.db.execute(
        text("UPDATE game_source_observations SET observed_title='Minecraft 2', revision=2")
    )
    stored = (
        await world.db.execute(
            select(
                GameSourceObservation.observed_title, GameSourceObservation.observed_facts
            ).where(GameSourceObservation.id == observation)
        )
    ).one()
    assert stored == ("Minecraft 2", {"playtime_seconds": 720000})


async def test_unavailable_since_requires_unavailable(world):
    user = await world.user()
    connection = await world.connection(user)
    await rejected(
        world,
        insert(GameSourceObservation).values(
            id=uuid4(),
            user_id=user,
            connection_id=connection,
            external_id="x",
            availability="available",
            unavailable_since=5,
        ),
    )


# --- links, archive and disconnect ------------------------------------------


async def test_an_observation_has_one_open_link_and_moving_keeps_history(world):
    user = await world.user()
    first, second = await world.game(user), await world.game(user)
    connection = await world.connection(user)
    observation = await world.observation(user, connection)
    link = uuid4()
    await world.db.execute(
        insert(GameSourceObservationLink).values(
            id=link, user_id=user, observation_id=observation, game_id=first, linked_at=10
        )
    )
    await rejected(
        world,
        insert(GameSourceObservationLink).values(
            id=uuid4(), user_id=user, observation_id=observation, game_id=second
        ),
    )
    await world.db.execute(
        text("UPDATE game_source_observation_links SET unlinked_at=20 WHERE id=:id"),
        {"id": link},
    )
    await world.db.execute(
        insert(GameSourceObservationLink).values(
            id=uuid4(), user_id=user, observation_id=observation, game_id=second, linked_at=20
        )
    )
    total = await world.db.scalar(
        select(func.count())
        .select_from(GameSourceObservationLink)
        .where(GameSourceObservationLink.observation_id == observation)
    )
    assert total == 2
    await rejected(
        world,
        insert(GameSourceObservationLink).values(
            id=uuid4(),
            user_id=user,
            observation_id=observation,
            game_id=first,
            linked_at=30,
            unlinked_at=25,
        ),
    )


async def test_purging_a_game_keeps_the_observation_and_its_source_identity(world):
    user = await world.user()
    game = await world.game(user)
    connection = await world.connection(user)
    observation = await world.observation(user, connection, "guid-9", observed_title="Gone")
    await world.db.execute(
        insert(GameSourceObservationLink).values(
            id=uuid4(), user_id=user, observation_id=observation, game_id=game
        )
    )
    await world.db.execute(delete(Game).where(Game.id == game))
    assert (
        await world.db.scalar(
            select(GameSourceObservation.external_id).where(GameSourceObservation.id == observation)
        )
        == "guid-9"
    )
    assert (
        await world.db.scalar(
            select(func.count())
            .select_from(GameSourceObservationLink)
            .where(GameSourceObservationLink.observation_id == observation)
        )
        == 0
    )


async def test_disconnect_and_archive_are_flags_and_never_remove_observations(world):
    user = await world.user()
    connection = await world.connection(user)
    observation = await world.observation(user, connection)
    await world.db.execute(
        text("UPDATE game_source_connections SET disconnected_at=5 WHERE id=:id"),
        {"id": connection},
    )
    await world.db.execute(
        text("UPDATE game_source_observations SET archived_at=6 WHERE id=:id"), {"id": observation}
    )
    assert (
        await world.db.scalar(
            select(GameSourceObservation.archived_at).where(GameSourceObservation.id == observation)
        )
        == 6
    )
    # a connection that still has observations cannot be deleted
    await rejected(world, delete(GameSourceConnection).where(GameSourceConnection.id == connection))


async def test_an_umbrella_with_identities_cannot_be_deleted(world):
    user = await world.user()
    game, umbrella = await world.game(user), await world.umbrella(user)
    await world.db.execute(
        insert(GameIdentity).values(game_id=game, user_id=user, umbrella_id=umbrella)
    )
    await rejected(world, delete(GameUmbrella).where(GameUmbrella.id == umbrella))


# --- relationships -----------------------------------------------------------


async def test_relationship_edges_are_directed_unique_and_never_self_referential(world):
    user = await world.user()
    base, dlc = await world.game(user, "Cyberpunk 2077"), await world.game(user, "Phantom Liberty")
    edge = {"user_id": user, "kind": "dlc_of", "from_game_id": base, "to_game_id": dlc}
    await world.db.execute(insert(GameRelationship).values(id=uuid4(), **edge))
    await rejected(world, insert(GameRelationship).values(id=uuid4(), **edge))
    # the reverse direction is a different edge
    await world.db.execute(
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="dlc_of", from_game_id=dlc, to_game_id=base
        )
    )
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="related_to", from_game_id=base, to_game_id=base
        ),
    )
    # an ended edge may be recreated; history keeps the old one
    await world.db.execute(
        text("UPDATE game_relationships SET ended_at=9 WHERE kind='dlc_of' AND from_game_id=:f"),
        {"f": base},
    )
    await world.db.execute(insert(GameRelationship).values(id=uuid4(), **edge))


async def test_relationship_endpoints_are_exactly_one_game_or_umbrella(world):
    user = await world.user()
    game, other = await world.game(user), await world.game(user)
    umbrella = await world.umbrella(user)
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="related_to", from_game_id=game
        ),
    )
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(),
            user_id=user,
            kind="related_to",
            from_game_id=game,
            from_umbrella_id=umbrella,
            to_game_id=other,
        ),
    )
    # a game may relate to an umbrella (a series membership)
    series = await world.umbrella(user, "Series", kind="series")
    await world.db.execute(
        insert(GameRelationship).values(
            id=uuid4(),
            user_id=user,
            kind="series_member",
            from_game_id=game,
            to_umbrella_id=series,
        )
    )


async def test_same_game_is_structural_single_target_and_same_level(world):
    user = await world.user()
    a, b, c = [await world.game(user) for _ in range(3)]
    await world.db.execute(
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="same_game", from_game_id=a, to_game_id=b
        )
    )
    # a is already absorbed into b
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="same_game", from_game_id=a, to_game_id=c
        ),
    )
    umbrella = await world.umbrella(user)
    await rejected(
        world,
        insert(GameRelationship).values(
            id=uuid4(), user_id=user, kind="same_game", from_game_id=c, to_umbrella_id=umbrella
        ),
    )


async def test_custom_relationships_need_a_label_and_predefined_ones_must_not_have_one(world):
    user = await world.user()
    a, b = await world.game(user), await world.game(user)
    base = {"id": uuid4, "user_id": user, "from_game_id": a, "to_game_id": b}

    def edge(**fields):
        return insert(GameRelationship).values(**{**base, "id": uuid4(), **fields})

    await rejected(world, edge(kind="custom"))
    await rejected(world, edge(kind="custom", label="   "))
    await rejected(world, edge(kind="related_to", label="Cozy games"))
    await world.db.execute(edge(kind="custom", label="Cozy games", display="nest"))
    # distinct labels are distinct edges; the same label is not repeated
    await world.db.execute(edge(kind="custom", label="Co-op night"))
    await rejected(world, edge(kind="custom", label="Cozy games"))


# --- decisions ---------------------------------------------------------------


async def test_pair_decisions_are_ordered_and_have_one_current_row_per_pair(world):
    user = await world.user()
    one, two = sorted([await world.game(user), await world.game(user)])
    pair = {
        "user_id": user,
        "subject_kind": "identity_pair",
        "first_game_id": one,
        "second_game_id": two,
        "outcome": "confirmed_separate",
        "actor_kind": "user",
    }
    await rejected(
        world,
        insert(GameIdentityDecision).values(
            id=uuid4(), **{**pair, "first_game_id": two, "second_game_id": one}
        ),
    )
    await world.db.execute(insert(GameIdentityDecision).values(id=uuid4(), **pair))
    await rejected(world, insert(GameIdentityDecision).values(id=uuid4(), **pair))
    # superseding the current decision records the new judgement and keeps history
    await world.db.execute(
        text("UPDATE game_identity_decisions SET superseded_at=7 WHERE first_game_id=:f"),
        {"f": one},
    )
    await world.db.execute(
        insert(GameIdentityDecision).values(
            id=uuid4(), **{**pair, "outcome": "confirmed_same", "prior_state": {"was": "separate"}}
        )
    )
    assert (
        await world.db.scalar(
            select(func.count())
            .select_from(GameIdentityDecision)
            .where(GameIdentityDecision.first_game_id == one)
        )
        == 2
    )


async def test_a_new_third_record_is_not_covered_by_an_earlier_pair_decision(world):
    user = await world.user()
    one, two, three = sorted([await world.game(user) for _ in range(3)])

    def decision(first, second):
        return insert(GameIdentityDecision).values(
            id=uuid4(),
            user_id=user,
            subject_kind="identity_pair",
            first_game_id=first,
            second_game_id=second,
            outcome="confirmed_separate",
            actor_kind="user",
        )

    await world.db.execute(decision(one, two))
    await world.db.execute(decision(one, three))


async def test_only_a_person_confirms_and_automatic_decisions_must_be_explained(world):
    user = await world.user()
    one, two = sorted([await world.game(user), await world.game(user)])
    pair = {
        "id": uuid4,
        "user_id": user,
        "subject_kind": "identity_pair",
        "first_game_id": one,
        "second_game_id": two,
    }

    def decision(**fields):
        return insert(GameIdentityDecision).values(**{**pair, "id": uuid4(), **fields})

    for outcome in ("confirmed_same", "confirmed_separate"):
        for actor in ("automatic", "source"):
            await rejected(world, decision(outcome=outcome, actor_kind=actor))
    await rejected(world, decision(outcome="automatic_same", actor_kind="automatic"))
    await rejected(world, decision(outcome="automatic_same", actor_kind="user", reason="x"))
    await rejected(
        world,
        decision(
            outcome="automatic_same",
            actor_kind="automatic",
            reason="same title, date, edition and platform",
            rule_version="v1",
            confidence=1.5,
        ),
    )
    await world.db.execute(
        decision(
            outcome="automatic_same",
            actor_kind="automatic",
            reason="same title, date, edition and platform",
            rule_version="v1",
            confidence=0.97,
            evidence={"title": True, "platform": True},
        )
    )


async def test_each_decision_subject_uses_only_its_own_columns(world):
    user = await world.user()
    game, other = await world.game(user), await world.game(user)
    umbrella = await world.umbrella(user)
    observation = await world.observation(user, await world.connection(user))

    def decision(**fields):
        return insert(GameIdentityDecision).values(
            id=uuid4(), user_id=user, outcome="unresolved", actor_kind="automatic", **fields
        )

    await rejected(world, decision(subject_kind="identity_pair", first_game_id=game))
    await rejected(
        world,
        decision(
            subject_kind="observation_identity",
            observation_id=observation,
            first_game_id=game,
            second_game_id=other,
        ),
    )
    await world.db.execute(
        decision(
            subject_kind="observation_identity", observation_id=observation, second_game_id=game
        )
    )
    await world.db.execute(
        decision(subject_kind="identity_umbrella", first_game_id=game, second_umbrella_id=umbrella)
    )


async def test_purging_a_game_removes_its_decisions_and_clears_pointers_to_them(world):
    user = await world.user()
    one, two, three = sorted([await world.game(user) for _ in range(3)])
    decision = uuid4()
    await world.db.execute(
        insert(GameIdentityDecision).values(
            id=decision,
            user_id=user,
            subject_kind="identity_pair",
            first_game_id=one,
            second_game_id=two,
            outcome="confirmed_same",
            actor_kind="user",
        )
    )
    relationship = uuid4()
    await world.db.execute(
        insert(GameRelationship).values(
            id=relationship,
            user_id=user,
            kind="same_game",
            from_game_id=three,
            to_game_id=one,
            decision_id=decision,
            origin="user",
        )
    )
    await world.db.execute(delete(Game).where(Game.id == two))
    assert (
        await world.db.scalar(
            select(func.count())
            .select_from(GameIdentityDecision)
            .where(GameIdentityDecision.id == decision)
        )
        == 0
    )
    assert (
        await world.db.scalar(
            select(GameRelationship.decision_id).where(GameRelationship.id == relationship)
        )
        is None
    )


# --- counters, adjustments, fields, manual facts ----------------------------


async def test_playnite_and_manual_observations_share_one_counter_without_double_counting(world):
    user = await world.user()
    game = await world.game(user)
    playnite = await world.observation(
        user,
        await world.connection(user),
        "guid-1",
        observed_facts={"playtime_seconds": 720000},
    )
    manual = await world.observation(
        user,
        await world.connection(user, "manual", "local", kind="manual"),
        "entry-1",
        observed_facts={"playtime_seconds": 702000},
    )
    counter = await world.counter(user, game)
    for observation, priority in ((playnite, 0), (manual, 1)):
        await world.db.execute(
            insert(GameMetricCounterMember).values(
                id=uuid4(),
                user_id=user,
                counter_id=counter,
                metric="playtime",
                observation_id=observation,
                priority=priority,
            )
        )
    # one open membership per observation and metric
    await rejected(
        world,
        insert(GameMetricCounterMember).values(
            id=uuid4(),
            user_id=user,
            counter_id=await world.counter(user, game),
            metric="playtime",
            observation_id=playnite,
        ),
    )
    # the same observation may feed a different metric
    await world.db.execute(
        insert(GameMetricCounterMember).values(
            id=uuid4(),
            user_id=user,
            counter_id=await world.counter(user, game, "last_played"),
            metric="last_played",
            observation_id=playnite,
        )
    )


async def test_member_metric_must_match_its_counter(world):
    user = await world.user()
    game = await world.game(user)
    observation = await world.observation(user, await world.connection(user))
    counter = await world.counter(user, game, "playtime")
    await rejected(
        world,
        insert(GameMetricCounterMember).values(
            id=uuid4(),
            user_id=user,
            counter_id=counter,
            metric="achievements",
            observation_id=observation,
        ),
    )


async def test_adjustment_is_an_offset_or_an_override_with_one_current_row(world):
    user = await world.user()
    game = await world.game(user)
    counter = await world.counter(user, game)
    base = {"id": uuid4, "user_id": user, "counter_id": counter, "metric": "playtime"}

    def adjustment(**fields):
        return insert(GameMetricAdjustment).values(**{**base, "id": uuid4(), **fields})

    await rejected(world, adjustment(kind="offset"))
    await rejected(world, adjustment(kind="offset", offset_value=-36000, override_value=1))
    await rejected(world, adjustment(kind="override", offset_value=5))
    await world.db.execute(adjustment(kind="offset", offset_value=-36000, base_value=720000))
    await rejected(world, adjustment(kind="offset", offset_value=-1))
    await world.db.execute(
        text("UPDATE game_metric_adjustments SET superseded_at=3 WHERE counter_id=:c"),
        {"c": counter},
    )
    await world.db.execute(adjustment(kind="override", override_value=684000))


async def test_field_values_have_one_current_value_and_user_values_have_no_observation(world):
    user = await world.user()
    game = await world.game(user)
    observation = await world.observation(user, await world.connection(user))

    def value(**fields):
        return insert(GameFieldValue).values(
            id=uuid4(), user_id=user, game_id=game, field="title", **fields
        )

    await world.db.execute(value(value="Minecraft", origin="user", locked=True, lock_origin="user"))
    await rejected(world, value(value="Other", origin="user"))
    await rejected(
        world, value(value="Other", state="superseded", origin="user", observation_id=observation)
    )
    await rejected(world, value(value="Other", state="superseded", origin="source"))
    await rejected(world, value(origin="user", state="superseded", locked=True))
    await rejected(world, value(origin="user", state="superseded", lock_origin="user"))
    await rejected(world, value(origin="user", state="superseded", confidence=2))
    # a source's rename of a locked title waits as one proposal per observation
    await world.db.execute(
        value(
            value="Minecraft: Java", state="proposed", origin="source", observation_id=observation
        )
    )
    await rejected(
        world,
        value(value="Minecraft 2", state="proposed", origin="source", observation_id=observation),
    )


async def test_manual_achievements_target_one_game_or_umbrella_and_stay_apart_from_providers(world):
    user = await world.user()
    game, umbrella = await world.game(user), await world.umbrella(user)

    def achievement(**fields):
        return insert(GameManualAchievement).values(id=uuid4(), user_id=user, **fields)

    await world.db.execute(
        achievement(game_id=game, title="Beat the Ender Dragon", achieved=True, achieved_at=5)
    )
    await world.db.execute(
        achievement(umbrella_id=umbrella, title="Any-version run", match_key="run")
    )
    await rejected(world, achievement(title="Nowhere"))
    await rejected(world, achievement(game_id=game, umbrella_id=umbrella, title="Both"))
    await rejected(world, achievement(game_id=game, title="  "))
    await rejected(world, achievement(game_id=game, title="Unearned", achieved_at=5))
    # duplicate titles are allowed: titles are never an identity
    await world.db.execute(achievement(game_id=game, title="Beat the Ender Dragon"))


# --- compatibility -----------------------------------------------------------


async def test_existing_games_keep_working_without_any_identity_rows(world):
    user = await world.user()
    game = await world.game(user, source="Steam", external_id="570", playnite_guid=uuid4())
    loaded = await world.db.get(Game, game)
    assert loaded is not None and loaded.external_id == "570"
    assert (
        await world.db.scalar(
            select(func.count()).select_from(GameIdentity).where(GameIdentity.game_id == game)
        )
        == 0
    )


async def test_deleting_a_user_removes_every_identity_row(world):
    user = await world.user()
    game, umbrella = await world.game(user), await world.umbrella(user)
    connection = await world.connection(user)
    observation = await world.observation(user, connection)
    await world.db.execute(
        insert(GameIdentity).values(game_id=game, user_id=user, umbrella_id=umbrella)
    )
    await world.db.execute(
        insert(GameSourceObservationLink).values(
            id=uuid4(), user_id=user, observation_id=observation, game_id=game
        )
    )
    counter = await world.counter(user, game)
    await world.db.execute(
        insert(GameMetricCounterMember).values(
            id=uuid4(),
            user_id=user,
            counter_id=counter,
            metric="playtime",
            observation_id=observation,
        )
    )
    await world.db.execute(delete(User).where(User.id == user))
    for model in (
        GameUmbrella,
        GameIdentity,
        GameSourceConnection,
        GameSourceObservation,
        GameSourceObservationLink,
        GameMetricCounter,
        GameMetricCounterMember,
    ):
        assert (
            await world.db.scalar(
                select(func.count()).select_from(model).where(model.user_id == user)
            )
            == 0
        )
