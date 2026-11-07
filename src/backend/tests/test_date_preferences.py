"""How release dates are shown (CONTRACTS-v1 C5.5): `date_display` and `partial_date_style`.

The values are stored with the account's other preferences and read back from PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.auth import hash_password
from src.core.preferences import load_preferences, save_preferences, validate_preference
from src.database.models.user import User
from src.database.session import SessionLocal


@pytest.fixture
async def user_id():
    async with SessionLocal() as db:
        user = User(
            username=f"dates_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash=hash_password("Test-password!9"),
        )
        db.add(user)
        await db.commit()
        identity = user.id
    yield identity
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


async def test_an_account_starts_with_local_dates_and_long_month_names(user_id):
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert (prefs["date_display"], prefs["partial_date_style"]) == ("locale", "long")


async def test_date_choices_are_saved_and_read_back(user_id):
    async with SessionLocal() as db:
        await save_preferences(db, user_id, {"date_display": "iso", "partial_date_style": "short"})
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert (prefs["date_display"], prefs["partial_date_style"]) == ("iso", "short")
    # other preferences keep their defaults
    assert prefs["title_language"] == "english"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("date_display", "us"),
        ("date_display", True),
        ("date_display", None),
        ("partial_date_style", "medium"),
        ("partial_date_style", 1),
    ],
)
def test_unknown_date_choices_are_refused(key, value):
    with pytest.raises(ValueError, match=key):
        validate_preference(key, value)
