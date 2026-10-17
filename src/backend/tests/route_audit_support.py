"""Shared fixtures for the route-by-route authorization audit.

Two ordinary users (A attacker, B victim) and an administrator are created with
real rows; B owns at least one object of every user-scoped type so a request
from A that names B's identifiers can be compared with B's own response.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from fastapi.routing import APIRoute

from src.core.auth import create_api_key
from src.database.models.achievement import Achievement
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.calendar_event import CalendarEvent
from src.database.models.game import Game, GameStatus
from src.database.models.game_archive import GameArchive, GameArchiveVersion
from src.database.models.game_checklist_item import GameChecklistItem
from src.database.models.game_file_item import GameFileItem
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.game_profile import GameProfile
from src.database.models.inbox_item import InboxItem
from src.database.models.media_extras import (
    ActivityEventType,
    ActivityLog,
    MediaList,
    MediaListItem,
    MediaType,
    RewatchLog,
)
from src.database.models.media_item import MediaItem
from src.database.models.movies import Movie
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_permissions import PluginClientIdentity
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app


@dataclass
class Tenant:
    user: User
    api_key: str
    ids: dict[str, str] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.api_key}


async def _user(db, name: str, *, admin: bool = False) -> Tenant:
    user = User(
        username=f"audit_{name}_{uuid4().hex[:8]}",
        email=f"{uuid4()}@example.test",
        password_hash="x",
        is_active=True,
        is_admin=admin,
    )
    db.add(user)
    await db.flush()
    raw, prefix, digest = create_api_key()
    key = UserApiKey(user_id=user.id, name="audit", key_prefix=prefix, key_hash=digest)
    db.add(key)
    await db.flush()
    tenant = Tenant(user, raw)
    tenant.ids["key_id"] = str(key.id)
    return tenant


async def seed_owner(db, tenant: Tenant) -> None:
    """Give one user an object of every user-scoped type reachable through ordinary routes."""
    uid = tenant.user.id
    ids = tenant.ids
    game = Game(
        user_id=uid,
        title="Owner game",
        sort_title="owner game",
        folder_location=f"audit_{uuid4().hex}",
        status=GameStatus.BACKLOG,
    )
    movie = Movie(user_id=uid, title="Owner movie", sort_title="owner movie")
    show = TVShow(user_id=uid, title="Owner show", sort_title="owner show")
    anime = Anime(user_id=uid, title="Owner anime", sort_title="owner anime")
    db.add_all([game, movie, show, anime])
    await db.flush()
    season = TVSeason(show_id=show.id, season_number=1)
    anime_season = AnimeSeason(show_id=anime.id, season_number=1)
    db.add_all([season, anime_season])
    await db.flush()
    episode = TVEpisode(season_id=season.id, episode_number=1)
    anime_episode = AnimeEpisode(season_id=anime_season.id, episode_number=1)
    media_list = MediaList(user_id=uid, name="Owner list")
    db.add_all([episode, anime_episode, media_list])
    await db.flush()
    list_item = MediaListItem(list_id=media_list.id, media_type=MediaType.MOVIE, media_id=movie.id)
    profile = GameProfile(game_id=game.id, name="Owner profile")
    checklist = GameChecklistItem(game_id=game.id, text="Owner step")
    achievement = Achievement(game_id=game.id, provider="manual", external_id="a1", name="Ach")
    archive = GameArchive(game_id=game.id, kind="save", name="Owner archive")
    file_item = GameFileItem(game_id=game.id, kind="doc", filename="owner.txt")
    shot = MediaItem(game_id=game.id, kind="screenshot", filename="owner.png")
    note = GameNoteDetail(game_id=game.id, name="owner-note")
    db.add_all([list_item, profile, checklist, achievement, archive, file_item, shot, note])
    await db.flush()
    version = GameArchiveVersion(archive_id=archive.id, filename="v1.zip", size=1)
    rewatch = RewatchLog(
        user_id=uid, media_type=MediaType.MOVIE, media_id=movie.id, finished_on=date(2024, 1, 1)
    )
    activity = ActivityLog(
        user_id=uid,
        media_type=MediaType.MOVIE,
        media_id=movie.id,
        media_title="Owner movie",
        event_type=next(iter(ActivityEventType)),
        event_date=date(2024, 1, 1),
    )
    event = CalendarEvent(user_id=uid, title="Owner event", event_date=date(2024, 1, 1))
    inbox = InboxItem(user_id=uid, kind="screenshot", filename="owner.png")
    notice = Notification(
        user_id=uid,
        kind="test",
        media_type="movie",
        media_id=movie.id,
        title="t",
        body="b",
        event_at=1,
        dedupe_key=f"audit:{uuid4()}",
    )
    session = UserSession(user_id=uid, token_hash=uuid4().hex + uuid4().hex, expires_at=2**33)
    destinations = {
        kind: NotificationDestination(
            user_id=uid,
            provider_id=kind,
            endpoint_key=f"{kind}:{uuid4()}",
            kind=kind,
            display_name=kind,
        )
        for kind in ("email", "webhook", "browser", "plugin")
    }
    device = PluginClientIdentity(
        plugin_id="audit.plugin",
        installation_id=uuid4(),
        user_id=uid,
        device_id=uuid4(),
        name="Owner device",
        token_hash=uuid4().hex + uuid4().hex,
    )
    db.add_all(
        [version, rewatch, activity, event, inbox, notice, session, device, *destinations.values()]
    )
    await db.flush()
    ids.update(
        game_id=game.id,
        movie_id=movie.id,
        tv_id=show.id,
        anime_id=anime.id,
        tv_season_id=season.id,
        anime_season_id=anime_season.id,
        tv_episode_id=episode.id,
        anime_episode_id=anime_episode.id,
        list_id=media_list.id,
        list_item_id=list_item.id,
        profile_id=profile.id,
        checklist_id=checklist.id,
        achievement_id=achievement.id,
        archive_id=archive.id,
        archive_version_id=version.id,
        file_item_id=file_item.id,
        media_id=shot.id,
        rewatch_id=rewatch.id,
        activity_id=activity.id,
        event_id=event.id,
        notification_id=notice.id,
        session_id=session.id,
        client_id=device.id,
        user_id=uid,
    )
    for kind, row in destinations.items():
        ids[f"{kind}_destination_id"] = row.id
    ids.update({key: str(value) for key, value in ids.items()})


@dataclass
class Tenants:
    admin: Tenant
    attacker: Tenant
    victim: Tenant


async def create_tenants() -> Tenants:
    async with SessionLocal() as db:
        tenants = Tenants(
            await _user(db, "admin", admin=True),
            await _user(db, "attacker"),
            await _user(db, "victim"),
        )
        await seed_owner(db, tenants.victim)
        await seed_owner(db, tenants.attacker)
        await db.commit()
    return tenants


def _kind_for(path: str) -> str:
    for marker, kind in (
        ("/archives", "save"),
        ("/files", "doc"),
        ("/screenshots", "screenshot"),
        ("/inbox", "screenshot"),
    ):
        if marker in path:
            return kind
    return "doc"


IDENTITY_RECORD_IDENTIFIERS = (
    "observation_id",
    "relationship_id",
    "counter_id",
    "decision_id",
)


def resolve_path(template: str, ids: dict[str, str]) -> str | None:
    """Fill a route template with one tenant's identifiers; None if a parameter is unknown."""
    anime = "/api/anime" in template
    destination_kind = next(
        (
            kind
            for kind in ("email", "webhook", "browser", "plugin")
            if f"{kind}-destinations" in template
        ),
        "email",
    )
    values = {
        "game_id": ids["game_id"],
        "movie_id": ids["movie_id"],
        "show_id": ids["anime_id" if anime else "tv_id"],
        "season_id": ids["anime_season_id" if anime else "tv_season_id"],
        "episode_id": ids["anime_episode_id" if anime else "tv_episode_id"],
        "list_id": ids["list_id"],
        "profile_id": ids["profile_id"],
        "archive_id": ids["archive_id"],
        "version_id": ids["archive_version_id"],
        "note_name": "owner-note",
        "asset_kind": "key_art",
        "media_id": ids["media_id"],
        "notification_id": ids["notification_id"],
        "destination_id": ids[f"{destination_kind}_destination_id"],
        "event_id": ids["event_id"],
        "entry_id": ids["activity_id"],
        "rewatch_id": ids["rewatch_id"],
        "achievement_id": ids["achievement_id"],
        "user_id": ids["user_id"],
        "key_id": ids["key_id"],
        "client_id": ids["client_id"],
        "media_type": "movie",
        "which": "poster",
        "kind": _kind_for(template),
        "filename": "owner.txt" if "/files" in template else "owner.png",
        "session_id": ids["session_id"] if "/sessions/me" in template else str(uuid4()),
    }
    # Game identity records (observations, counters, decisions, relationships) have no
    # fixture: a fresh identifier proves a caller cannot reach a record that is not theirs.
    for name in IDENTITY_RECORD_IDENTIFIERS:
        values[name] = str(uuid4())
    if "{item_id}" in template:
        values["item_id"] = ids[
            "list_item_id"
            if "/lists/" in template
            else "checklist_id"
            if "/checklist" in template
            else "file_item_id"
            if "/files" in template
            else "movie_id"
        ]
    missing = [name for name in re.findall(r"\{(\w+)", template) if name not in values]
    if missing:
        return None
    return re.sub(r"\{(\w+)[^}]*\}", lambda match: values[match.group(1)], template)


def walk_routes(router, prefix: str = ""):
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from walk_routes(route.original_router, prefix + route.include_context.prefix)


def example_value(schema: dict, defs: dict, ids: dict[str, str]):
    """A minimal instance of a JSON schema, good enough to pass request validation."""
    if "$ref" in schema:
        return example_value(defs[schema["$ref"].split("/")[-1]], defs, ids)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [item for item in schema[key] if item.get("type") != "null"]
            return example_value(options[0], defs, ids) if options else None
    if "allOf" in schema:
        return example_value(schema["allOf"][0], defs, ids)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        props = schema.get("properties", {})
        return {name: example_value(props[name], defs, ids) for name in required if name in props}
    if kind == "array":
        return []
    if kind == "integer":
        return max(int(schema.get("minimum", 1)), 1)
    if kind == "number":
        return max(float(schema.get("minimum", 1)), 1.0)
    if kind == "boolean":
        return False
    if schema.get("format") == "uuid":
        return ids["game_id"]
    if schema.get("format") == "date":
        return "2024-01-01"
    if schema.get("format") in ("uri", "url"):
        return "https://example.test/x"
    return "x" * max(int(schema.get("minLength", 1)), 1)


_DEPENDENCY_AUTHORITY = (
    ("get_current_admin", "admin"),
    ("get_plugin_manager_admin", "plugin-admin"),
    ("get_plugin_manager_reader", "plugin-reader"),
    ("browser_user", "browser-session"),
    ("delegated_actor", "delegated-grant"),
    ("plugin_gateway", "plugin-runtime"),
    ("plugin_backend_route", "plugin-route"),
    ("plugin_host_backend_route", "plugin-route"),
    ("get_current_user", "user"),
)
# Routes that authenticate inline, or on a credential the dependency graph cannot show.
INLINE_AUTHORITY = {
    ("GET", "/api/setup/configuration"): "setup-or-admin",
    ("GET", "/api/internal/real-ip"): "local-or-admin",
}
_PUBLIC_APP_OPEN = {"config", "asset", "bootstrap"}


def _dependency_names(dependant) -> set[str]:
    names = {getattr(dependant.call, "__name__", "")} if dependant.call else set()
    for sub in dependant.dependencies:
        names |= _dependency_names(sub)
    return names


def route_authority(method: str, path: str, route: APIRoute) -> str:
    """The credential class a route demands, derived from what FastAPI will really run."""
    if (method, path) in INLINE_AUTHORITY:
        return INLINE_AUTHORITY[(method, path)]
    if route.endpoint.__module__.endswith("public_apps"):
        return "public" if route.endpoint.__name__ in _PUBLIC_APP_OPEN else "public-app-session"
    names = _dependency_names(route.dependant)
    for dependency, authority in _DEPENDENCY_AUTHORITY:
        if dependency in names:
            return authority
    return "public"


def live_matrix() -> dict[tuple[str, str], str]:
    """Every (method, path) the application serves, with its enforced authority."""
    matrix: dict[tuple[str, str], str] = {}
    for path, route in walk_routes(app.router):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            matrix[(method, path)] = route_authority(method, path, route)
    return matrix
