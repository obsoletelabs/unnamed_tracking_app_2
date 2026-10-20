"""Route-by-route authorization audit of the ordinary (non-integration) application API.

The published matrix (wiki/docs/development/api-access-matrix.md) must name every route
and the credential class FastAPI really enforces; the sweeps below then prove, over real
HTTP and PostgreSQL, that credential classes, administrator boundaries and manipulated
object identifiers behave as that matrix says.
"""

import re
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.database.models.game import Game
from src.database.models.inbox_item import InboxItem
from src.database.models.media_extras import MediaList, RewatchLog
from src.database.models.notification import Notification
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app
from tests.route_audit_support import (
    create_tenants,
    example_value,
    live_matrix,
    resolve_path,
    walk_routes,
)

MATRIX_DOC = Path(__file__).resolve().parents[3] / "wiki/docs/development/api-access-matrix.md"
ROW = re.compile(r"^\| (GET|POST|PUT|PATCH|DELETE) \| `([^`]+)` \| ([a-z-]+) \|", re.MULTILINE)
# Plugin-defined routes are dispatched by the catch-all and authenticated per declaration.
CATCH_ALL = "route_path"
# These accept a credential other than an ordinary user and are checked elsewhere.
NOT_USER_CREDENTIAL = {"public", "plugin-runtime", "plugin-route", "public-app-session"}
# Authorities that ordinary user credentials can never satisfy.
ADMIN_CLASSES = {"admin", "plugin-admin"}
# Authorities an ordinary user satisfies; "user-or-app" also takes a scoped app grant.
USER_CLASSES = {"user", "user-or-app"}


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """Keep every handler that writes under the data root out of the real /data."""
    for name, module in list(sys.modules.items()):
        if name.startswith("src.") and hasattr(module, "_DATA_ROOT"):
            monkeypatch.setattr(module, "_DATA_ROOT", tmp_path)


@pytest.fixture
async def tenants():
    tenants = await create_tenants()
    yield tenants
    async with SessionLocal() as db:
        await db.execute(
            delete(User).where(
                User.id.in_(
                    [tenants.admin.user.id, tenants.attacker.user.id, tenants.victim.user.id]
                )
            )
        )
        await db.commit()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


def generic_path(template: str) -> str:
    return re.sub(r"\{[^}]*\}", str(uuid4()), template)


def test_published_matrix_names_every_route_with_its_enforced_authority():
    if not MATRIX_DOC.exists():
        pytest.skip("the wiki is not mounted in this container")
    published = {(m, p): a for m, p, a in ROW.findall(MATRIX_DOC.read_text())}
    live = live_matrix()
    assert sorted(set(live) - set(published)) == [], "routes missing from the access matrix"
    assert sorted(set(published) - set(live)) == [], "matrix lists routes that do not exist"
    assert {key: (published[key], live[key]) for key in live if published[key] != live[key]} == {}


async def test_anonymous_requests_never_reach_authenticated_handlers(client):
    leaked = []
    for (method, path), authority in live_matrix().items():
        if authority in NOT_USER_CREDENTIAL or CATCH_ALL in path or authority.endswith("or-admin"):
            continue
        if authority in {"setup-or-admin"}:
            continue
        response = await client.request(method, generic_path(path))
        if response.status_code not in (401, 403):
            leaked.append((method, path, authority, response.status_code))
    assert leaked == []


@pytest.mark.parametrize("prefix", ["utd_", "utp_", "utpm_"])
async def test_integration_and_management_credentials_are_not_user_credentials(client, prefix):
    leaked = []
    for (method, path), authority in live_matrix().items():
        if authority not in USER_CLASSES or (authority == "user-or-app" and prefix == "utd_"):
            continue
        response = await client.request(
            method,
            generic_path(path),
            headers={"Authorization": f"Bearer {prefix}{uuid4().hex}"},
        )
        if response.status_code != 403:
            leaked.append((method, path, response.status_code))
    assert leaked == []


async def test_ordinary_users_are_refused_on_every_administrator_route(client, tenants):
    leaked = []
    for (method, path), authority in live_matrix().items():
        if authority not in ADMIN_CLASSES:
            continue
        response = await client.request(
            method, generic_path(path), headers=tenants.attacker.headers
        )
        if response.status_code != 403:
            leaked.append((method, path, response.status_code))
    assert leaked == []


async def test_administrator_routes_are_reachable_by_an_administrator(client, tenants):
    """The previous check must fail because of authority, not because the route is dead."""
    reached = 0
    for (method, path), authority in live_matrix().items():
        if authority != "admin" or method != "GET" or "{" in path:
            continue
        response = await client.request(method, path, headers=tenants.admin.headers)
        assert response.status_code != 403, path
        reached += 1
    assert reached >= 5


def _sweep_requests(ids: dict[str, str]):
    skipped = ("/api/plugins", "/api/delegation", "/api/public-apps", "/api/auth/oidc")
    skipped += ("/api/themes", "/api/branding", "/pwa", "/api/calendar/feed/", "world-map")
    skipped += ("/api/settings/provider-credentials", "/api/metadata/sessions")
    for (method, template), authority in live_matrix().items():
        if authority not in USER_CLASSES or "{" not in template:
            continue
        if any(s in template for s in skipped):
            continue
        path = resolve_path(template, ids)
        route = next(r for p, r in walk_routes(app.router) if p == template)
        body = None
        if route.body_field is not None:
            from pydantic import TypeAdapter

            schema = TypeAdapter(route.body_field.field_info.annotation).json_schema()
            body = example_value(schema, schema.get("$defs", {}), ids)
        if path is not None and template.startswith("/api/calendar/sources/"):
            if template.endswith("/events"):
                path += "?start=2024-01-01&end=2024-01-31"
            elif template.endswith(("/overrides", "/copy")):
                body = {
                    "event_id": str(uuid4()),
                    "recurrence_key": "",
                    "occurrence_date": "2024-01-01",
                    "timezone": "UTC",
                }
                if template.endswith("/overrides"):
                    body.update(scope="occurrence", fields={"title": "Unauthorized change"})
        yield method, template, path, body


# DELETE/dismiss are idempotent no-ops for an identifier the caller cannot see.
IDEMPOTENT_NOOPS = {
    ("DELETE", "/api/notifications/{notification_id}"),
    ("POST", "/api/notifications/{notification_id}/dismiss"),
}
CATALOGUE_IDENTIFIERS = [
    "/api/anime/metadata/by-id/{anilist_id}",
    "/api/metadata/providers/{provider_id}/configuration",
    "/api/settings/notification-providers/{provider_id}",
    "/api/settings/user-jobs/{job_id}",
    "/api/settings/user-jobs/{job_id}/run",
    "/api/settings/user-jobs/{job_id}/{control}",
]
# The same filename exists in the attacker's own inbox, so the call legitimately succeeds.
OWN_INBOX_NAMES = {
    ("DELETE", "/api/media/inbox/{kind}/{filename}"),
    ("POST", "/api/media/inbox/{kind}/{filename}/restore"),
}


async def test_another_users_identifiers_are_rejected_on_every_ordinary_route(client, tenants):
    """Replace every path identifier with the victim's own; the attacker must get nothing."""
    unresolved = []
    exposed = []
    masked = []
    checked = 0
    for method, template, path, body in _sweep_requests(tenants.victim.ids):
        if path is None:
            unresolved.append(template)
            continue
        kwargs = {"json": body} if body is not None else {}
        response = await client.request(method, path, headers=tenants.attacker.headers, **kwargs)
        checked += 1
        if (method, template) in IDEMPOTENT_NOOPS | OWN_INBOX_NAMES:
            assert response.status_code in (200, 204)
        elif response.status_code not in (403, 404, 422):
            exposed.append((method, template, response.status_code))
        elif response.status_code == 422:
            masked.append((method, template))
    assert checked > 120
    assert exposed == []
    # These name instance catalogue entries (providers, scheduled jobs, AniList titles), not
    # rows owned by a user; their per-user state is keyed by the authenticated caller.
    assert sorted(unresolved) == CATALOGUE_IDENTIFIERS
    # Validation can hide an ownership check; keep that set small and covered by the
    # targeted tests below.
    assert len(masked) <= 25, masked


async def test_attacker_calls_leave_the_victims_data_untouched(client, tenants):
    victim = tenants.victim
    for method, _template, path, body in _sweep_requests(victim.ids):
        if path is None:
            continue
        kwargs = {"json": body} if body is not None else {}
        await client.request(method, path, headers=tenants.attacker.headers, **kwargs)
    async with SessionLocal() as db:
        game = await db.get(Game, victim.ids["game_id"])
        assert game is not None and game.deleted_at is None and game.title == "Owner game"
        assert await db.get(MediaList, victim.ids["list_id"]) is not None
        assert await db.get(RewatchLog, victim.ids["rewatch_id"]) is not None
        notice = await db.get(Notification, victim.ids["notification_id"])
        assert notice is not None and notice.read_at is None
        assert (
            await db.scalar(select(InboxItem).where(InboxItem.user_id == victim.user.id))
        ) is not None


async def test_victim_controls_prove_the_sweep_is_not_vacuous(client, tenants):
    """The same identifiers succeed for their owner, so the attacker's 404 is about ownership."""
    owner_ok = 0
    for method, _template, path, _body in _sweep_requests(tenants.victim.ids):
        if method != "GET" or path is None:
            continue
        response = await client.get(path, headers=tenants.victim.headers)
        owner_ok += response.status_code == 200
    assert owner_ok >= 25


async def test_unknown_app_credentials_are_refused_on_app_routes(client):
    """An app route checks the grant itself; a made-up `utd_` value is never a user."""
    leaked = []
    for (method, path), authority in live_matrix().items():
        if authority != "user-or-app":
            continue
        response = await client.request(
            method, generic_path(path), headers={"Authorization": f"Bearer utd_{uuid4().hex}"}
        )
        if response.status_code != 401:
            leaked.append((method, path, response.status_code))
    assert leaked == []
