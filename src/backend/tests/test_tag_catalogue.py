"""Tag catalogue: key folding, per-user isolation, defaults precedence and read-time resolution."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.core.auth import create_api_key
from src.database.models.auth import UserApiKey
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.tag_catalogue import (
    Resolved,
    TagResolver,
    count_resolved_tags,
    fold_key,
    load_resolver,
)
from src.main import app


async def _person(*, admin: bool = False) -> SimpleNamespace:
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        user = User(
            username=f"tags_{uuid4().hex[:10]}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
            is_active=True,
            is_admin=admin,
        )
        db.add(user)
        await db.flush()
        scopes = ["read", "write", "admin"] if admin else ["read", "write"]
        db.add(
            UserApiKey(user_id=user.id, name="k", key_prefix=prefix, key_hash=digest, scopes=scopes)
        )
        await db.commit()
        return SimpleNamespace(id=user.id, headers={"Authorization": "Bearer " + raw})


@pytest.fixture(name="http")
async def http_client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture(name="made")
async def made_people():
    people: list[SimpleNamespace] = []
    yield people
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([p.id for p in people])))
        await db.commit()


@pytest.fixture(name="me")
async def me_person(made):
    person = await _person()
    made.append(person)
    return person


@pytest.fixture(name="other")
async def other_person(made):
    person = await _person()
    made.append(person)
    return person


@pytest.fixture(name="admin")
async def admin_person(made):
    person = await _person(admin=True)
    made.append(person)
    return person


async def _game_with_tags(person: SimpleNamespace, tags: list[str]) -> None:
    async with SessionLocal() as db:
        db.add(
            Game(
                user_id=person.id,
                title="Tagged",
                sort_title="tagged",
                folder_location=f"Tagged-{uuid4().hex[:6]}",
                tags=tags,
            )
        )
        await db.commit()


async def _stored_tags(person: SimpleNamespace) -> list[str]:
    async with SessionLocal() as db:
        return list(await db.scalar(select(Game.tags).where(Game.user_id == person.id)) or [])


def test_keys_fold_case_unicode_and_whitespace_but_keep_namespaces():
    assert fold_key("  Rogue   Like ") == "rogue like"
    assert fold_key("ＲＰＧ") == "rpg"  # full-width forms fold under NFKC
    assert fold_key("Straße") == fold_key("STRASSE")
    assert fold_key("Genre: Action") != fold_key("Action")
    assert fold_key("日本語") == "日本語"


def test_one_record_counts_once_per_effective_label():
    tag_id = uuid4()
    resolver = TagResolver(
        user_tags={"a": Resolved("A", "user", tag_id), "b": Resolved("A", "user", tag_id)}
    )
    assert count_resolved_tags([["a", "b"], ["b"], ["c"]], resolver, 10) == [("A", 2), ("c", 1)]


async def test_create_returns_the_existing_tag_for_a_duplicate_key(http, me):
    first = await http.post("/api/tags", json={"name": "Roguelike"}, headers=me.headers)
    again = await http.post("/api/tags", json={"name": "  roguelike "}, headers=me.headers)
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert len((await http.get("/api/tags", headers=me.headers)).json()["tags"]) == 1


async def test_rename_changes_only_the_label_and_keeps_aliases(http, me):
    tag = (await http.post("/api/tags", json={"name": "Roguelike"}, headers=me.headers)).json()
    await http.post(
        f"/api/tags/{tag['id']}/aliases", json={"name": "Roguelite"}, headers=me.headers
    )
    renamed = await http.patch(
        f"/api/tags/{tag['id']}", json={"name": "Rogue-like"}, headers=me.headers
    )
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["label"] == "Rogue-like" and body["key"] == "roguelike"
    assert [a["label"] for a in body["aliases"]] == ["Roguelite"]


async def test_alias_rules_reject_self_chains_and_conflicts(http, me):
    a = (await http.post("/api/tags", json={"name": "Alpha"}, headers=me.headers)).json()
    b = (await http.post("/api/tags", json={"name": "Beta"}, headers=me.headers)).json()

    async def alias(tag, name):
        return await http.post(
            f"/api/tags/{tag['id']}/aliases", json={"name": name}, headers=me.headers
        )

    assert (await alias(a, "ALPHA")).status_code == 422
    # Beta is a tag of its own, so it cannot become an alias without archiving it first.
    assert (await alias(a, "beta")).status_code == 409
    assert (await alias(a, "Gamma")).status_code == 201
    assert (await alias(b, "gamma")).status_code == 409
    # An existing alias cannot be created as a tag either.
    assert (
        await http.post("/api/tags", json={"name": "Gamma"}, headers=me.headers)
    ).status_code == 409


async def test_delete_archives_the_tag_and_leaves_stored_tags_alone(http, me):
    await _game_with_tags(me, ["Roguelike", "roguelite"])
    tag = (await http.post("/api/tags", json={"name": "Roguelike"}, headers=me.headers)).json()
    await http.post(
        f"/api/tags/{tag['id']}/aliases", json={"name": "roguelite"}, headers=me.headers
    )
    assert (await http.delete(f"/api/tags/{tag['id']}", headers=me.headers)).status_code == 204
    assert (await http.get("/api/tags", headers=me.headers)).json()["tags"] == []
    assert await _stored_tags(me) == ["Roguelike", "roguelite"]
    assert (await http.delete(f"/api/tags/{tag['id']}", headers=me.headers)).status_code == 404


async def test_another_users_catalogue_is_invisible_and_untouchable(http, me, other):
    mine = (await http.post("/api/tags", json={"name": "Private"}, headers=me.headers)).json()
    assert (await http.get("/api/tags", headers=other.headers)).json()["tags"] == []
    refused = [
        await http.patch(f"/api/tags/{mine['id']}", json={"name": "Stolen"}, headers=other.headers),
        await http.delete(f"/api/tags/{mine['id']}", headers=other.headers),
        await http.post(
            f"/api/tags/{mine['id']}/aliases", json={"name": "x"}, headers=other.headers
        ),
    ]
    assert [r.status_code for r in refused] == [404, 404, 404]
    assert (await http.get("/api/tags", headers=me.headers)).json()["tags"][0]["label"] == "Private"


async def test_defaults_are_admin_only_but_visible_to_everyone(http, me, admin):
    denied = await http.post("/api/tags/defaults", json={"name": "RPG"}, headers=me.headers)
    assert denied.status_code == 403
    created = await http.post("/api/tags/defaults", json={"name": "RPG"}, headers=admin.headers)
    assert created.status_code == 201
    visible = (await http.get("/api/tags", headers=me.headers)).json()["defaults"]
    assert [t["label"] for t in visible] == ["RPG"]


async def test_user_choice_beats_a_default_and_suppression_shows_the_raw_spelling(http, me, admin):
    default = (
        await http.post("/api/tags/defaults", json={"name": "RPG"}, headers=admin.headers)
    ).json()
    await http.post(
        f"/api/tags/defaults/{default['id']}/aliases", json={"name": "jrpg"}, headers=admin.headers
    )

    async def label(raw):
        async with SessionLocal() as db:
            return (await load_resolver(db, me.id)).label_for(raw)

    assert await label("JRPG") == "RPG"
    private = (await http.post("/api/tags", json={"name": "Rpg"}, headers=me.headers)).json()
    await http.post(f"/api/tags/{private['id']}/aliases", json={"name": "JRPG"}, headers=me.headers)
    await http.patch(
        f"/api/tags/{private['id']}", json={"name": "Role-playing"}, headers=me.headers
    )
    assert await label("jrpg") == "Role-playing"

    # Suppression applies to defaults only; with no private choice the raw spelling shows.
    await http.delete(f"/api/tags/{private['id']}", headers=me.headers)
    suppress = {"tag": "jrpg", "suppressed": True}
    await http.put("/api/tags/suppressions", json=suppress, headers=me.headers)
    assert await label("jrpg") == "jrpg"
    await http.put(
        "/api/tags/suppressions", json={"tag": "jrpg", "suppressed": False}, headers=me.headers
    )
    assert await label("jrpg") == "RPG"


async def test_changing_a_default_never_replaces_a_private_choice(http, me, admin):
    default = (
        await http.post("/api/tags/defaults", json={"name": "RPG"}, headers=admin.headers)
    ).json()
    private = (await http.post("/api/tags", json={"name": "RPG"}, headers=me.headers)).json()
    await http.patch(f"/api/tags/{private['id']}", json={"name": "My RPGs"}, headers=me.headers)
    await http.patch(
        f"/api/tags/defaults/{default['id']}", json={"name": "Roleplay"}, headers=admin.headers
    )
    async with SessionLocal() as db:
        assert (await load_resolver(db, me.id)).label_for("rpg") == "My RPGs"


async def test_statistics_group_aliased_spellings_without_rewriting_tags(http, me):
    await _game_with_tags(me, ["Roguelike", "roguelite", "Indie"])
    before = (await http.get("/api/stats/overview", headers=me.headers)).json()["top_tags"]
    assert {t["label"] for t in before} == {"Roguelike", "roguelite", "Indie"}
    tag = (await http.post("/api/tags", json={"name": "Roguelike"}, headers=me.headers)).json()
    await http.post(
        f"/api/tags/{tag['id']}/aliases", json={"name": "roguelite"}, headers=me.headers
    )
    after = (await http.get("/api/stats/overview", headers=me.headers)).json()["top_tags"]
    assert {(t["label"], t["count"]) for t in after} == {("Roguelike", 1), ("Indie", 1)}
    assert await _stored_tags(me) == ["Roguelike", "roguelite", "Indie"]


async def test_suggestions_are_offered_but_never_applied(http, me):
    await _game_with_tags(me, ["Roguelike", "Roguelite", "Strategy"])
    body = (await http.get("/api/tags/suggestions", headers=me.headers)).json()["suggestions"]
    assert any({s["tag"], s["alias"]} == {"Roguelike", "Roguelite"} for s in body)
    assert (await http.get("/api/tags", headers=me.headers)).json()["tags"] == []


async def test_discover_creates_source_tags_once(http, me):
    await _game_with_tags(me, ["Indie", "indie", "Co-op"])
    assert (await http.post("/api/tags/discover", headers=me.headers)).json() == {"created": 2}
    assert (await http.post("/api/tags/discover", headers=me.headers)).json() == {"created": 0}
    kinds = {t["kind"] for t in (await http.get("/api/tags", headers=me.headers)).json()["tags"]}
    assert kinds == {"source"}


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
async def test_invalid_names_are_rejected(http, me, name):
    assert (
        await http.post("/api/tags", json={"name": name}, headers=me.headers)
    ).status_code == 422
