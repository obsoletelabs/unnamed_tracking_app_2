"""Untrusted input at account, search, storage-path and administrator-filter boundaries.

Hostile text is exercised as ordinary data: SQL-looking words are legitimate strings, while
identifiers that become credentials, paths or LIKE patterns are held to their field's type.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.api.routes import games
from src.core.auth import username_in_use, validate_username
from src.database.models.game import Game, GameStatus
from src.database.session import SessionLocal
from src.features.user_invitations import accept_invitation
from tests.test_account_security import accounts, sign_in  # noqa: F401  (fixture)
from tests.test_notification_email import mailbox  # noqa: F401  (fixture)
from tests.test_user_invitations import invitation, issue  # noqa: F401  (fixture)

HOSTILE_TEXT = [
    "NULL",
    "DROP",
    "'; DROP TABLE users; --",
    '" OR "1"="1',
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "../../etc/passwd",
    "..\\..\\windows\\system32",
    "%00",
    "\u202eevil\u202c",
    "a\u200bb",
    "x" * 5000,
]


async def test_sql_like_words_are_valid_usernames_but_markup_and_paths_are_not():
    assert validate_username("NULL") == "NULL"
    assert validate_username("DROP") == "DROP"
    for value in HOSTILE_TEXT[2:]:
        with pytest.raises(ValueError):
            validate_username(value)


async def test_usernames_differing_only_by_case_are_taken(accounts):  # noqa: F811
    existing = accounts.users[1].username
    async with SessionLocal() as db:
        assert await username_in_use(db, existing)
        assert await username_in_use(db, existing.upper())
        assert await username_in_use(db, f"  {existing.title()}  ")
        assert not await username_in_use(db, existing, exclude_user_id=accounts.users[1].id)
        assert not await username_in_use(db, "unused-" + uuid4().hex)


async def test_profile_rename_cannot_take_another_accounts_name_in_another_case(accounts):  # noqa: F811
    sign_in(accounts)
    response = await accounts.client.patch(
        "/api/auth/me", json={"username": accounts.users[1].username.upper()}
    )
    assert response.status_code == 409
    # The same combined message as any collision: the reply does not confirm which field.
    assert "username or email" in response.json()["detail"]
    own = accounts.users[0].username
    # Changing only the case of one's own name is not a collision.
    assert (
        await accounts.client.patch("/api/auth/me", json={"username": own.upper()})
    ).status_code == 200


@pytest.fixture
async def library(accounts):  # noqa: F811
    accounts.app.include_router(games.router)
    owner = accounts.users[0].id
    titles = ["100% Orange Juice", "Totally Different", "'; DROP TABLE games; --", "snake_case"]
    async with SessionLocal() as db:
        db.add_all(
            Game(
                user_id=owner,
                title=title,
                sort_title=title.lower(),
                folder_location=f"input_{uuid4().hex}",
                status=GameStatus.BACKLOG,
            )
            for title in titles
        )
        await db.commit()
    yield accounts
    async with SessionLocal() as db:
        await db.execute(delete(Game).where(Game.user_id == owner))
        await db.commit()


async def titles_for(library, search):
    sign_in(library)
    response = await library.client.get("/api/game/list", params={"search": search})
    assert response.status_code == 200
    return sorted(item["title"] for item in response.json())


async def test_game_search_treats_wildcards_and_sql_text_literally(library):
    assert await titles_for(library, "100%") == ["100% Orange Juice"]
    assert await titles_for(library, "%") == ["100% Orange Juice"]
    assert await titles_for(library, "snake_c") == ["snake_case"]
    assert await titles_for(library, "snakeXcase") == []
    assert await titles_for(library, "DROP TABLE games") == ["'; DROP TABLE games; --"]
    assert await titles_for(library, "' OR 1=1 --") == []
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Game)) >= 4


@pytest.mark.parametrize(
    "folder",
    ["../../etc", "..", "a/b", "a\\b", "/abs", "C:\\x", "x" * 400, "\u202eevil", "name\x00x"],
)
async def test_game_folder_names_cannot_leave_the_owners_storage(library, folder):
    sign_in(library)
    response = await library.client.post(
        "/api/game/create", json={"title": "Hostile folder", "folder_location": folder}
    )
    assert response.status_code == 422


async def test_stored_file_names_cannot_traverse(library):
    sign_in(library)
    async with SessionLocal() as db:
        game_id = await db.scalar(select(Game.id).where(Game.user_id == library.users[0].id))
    for name in ("..%2F..%2Fetc%2Fpasswd", "%2e%2e%2fsecret", "....//....//x"):
        for kind in ("screenshots/screenshot", "files/docs"):
            response = await library.client.get(f"/api/game/{game_id}/{kind}/{name}")
            assert response.status_code in {404, 422}


async def test_invitation_acceptance_rejects_a_name_taken_in_another_case(
    invitation,  # noqa: F811
):
    identity, token = await issue(invitation)
    taken = invitation.admin.hex
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="already in use"):
            await accept_invitation(db, identity, token, taken.upper(), "New-Password-789!")
        assert await accept_invitation(
            db, identity, token, "fresh-" + invitation.installation.hex, "New-Password-789!"
        )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "//evil.example/x.png",
        "https://user:pass@evil.example/x.png",
        "https://evil.example/x.png\r\nSet-Cookie: a=b",
        r"https://evil.example/\@internal/x.png",
    ],
)
def test_image_fallback_never_redirects_the_browser_to_a_non_web_address(url):
    from fastapi import HTTPException

    from src.api.image_responses import original_image_response

    with pytest.raises(HTTPException) as caught:
        original_image_response(url)
    assert caught.value.status_code == 404


def test_image_fallback_still_redirects_ordinary_addresses():
    from src.api.image_responses import original_image_response

    response = original_image_response("https://images.example/cover.png?size=big")
    assert response.status_code == 307
    assert response.headers["location"] == "https://images.example/cover.png?size=big"
