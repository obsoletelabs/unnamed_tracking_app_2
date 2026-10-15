"""Imports and public media PATCH requests keep finite, nullable values distinct."""

import io
import zipfile
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Column, Numeric, delete, select

from src.core.auth import AuthenticatedActor, get_current_actor, get_current_user
from src.database.models.anime import Anime
from src.database.models.movies import Movie
from src.database.models.tv_show import TVShow
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports.lists import parse_imdb, parse_letterboxd
from src.features.imports.restore import _convert, restore_media
from src.features.imports.yamtrack import build_yamtrack_item, parse_yamtrack
from src.main import app


@pytest.mark.parametrize(
    "score,expected",
    [
        ("NaN", None),
        ("Infinity", None),
        ("-Infinity", None),
        ("-1", Decimal("0.00")),
        ("99", Decimal("10.00")),
        ("8.765", Decimal("8.76")),
        ("0", Decimal("0.00")),
        ("1e999999", Decimal("10.00")),
        ("-1e999999", Decimal("0.00")),
        ("not a number", None),
        ("", None),
    ],
)
@pytest.mark.parametrize("source", ["imdb", "letterboxd", "yamtrack"])
def test_supported_list_sources_normalize_only_user_ratings(source, score, expected):
    if source == "imdb":
        data = f"Const,Title,Title Type,Your Rating\ntt1,Title,movie,{score}\n".encode()
        item = parse_imdb(data)[0][0]
        actual = item.rating
    elif source == "letterboxd":
        # Letterboxd's five-point source scale converts to the app's ten-point scale.
        raw = str(Decimal(score) / 2) if score in {"-1", "99", "8.765", "0"} else score
        data = f"Name,Year,Rating\nTitle,2020,{raw}\n".encode()
        actual = parse_letterboxd(data, "ratings.csv")[0].rating
    else:
        data = f"media_id,source,media_type,title,score\n1,tmdb,movie,Title,{score}\n".encode()
        actual = build_yamtrack_item(parse_yamtrack(data)[0]).rating_overall
    assert actual == expected


def test_restore_numeric_conversion_retains_unrelated_decimal_precision_and_zero():
    column = Column("unrelated_amount", Numeric(12, 4))
    assert _convert(column, "1.2345") == Decimal("1.2345")
    assert _convert(column, 0) == Decimal(0)
    assert _convert(column, None) is None


def test_letterboxd_zero_rating_keeps_priority_over_diary_ratings():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("ratings.csv", "Name,Year,Rating\nTitle,2020,0\n")
        archive.writestr("diary.csv", "Name,Year,Rating\nTitle,2020,5\n")
    assert parse_letterboxd(data.getvalue())[0].rating == Decimal("0.00")


@pytest.fixture
async def numeric_owner():
    async with SessionLocal() as db:
        user = User(
            username="numeric_" + uuid4().hex,
            email=uuid4().hex + "@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        owner_id = user.id
        records = {}
        for kind, model in (("movie", Movie), ("tv", TVShow), ("anime", Anime)):
            record = model(
                user_id=owner_id,
                title="Protected title",
                sort_title="Existing sort",
                favorite=True,
                rewatches=2,
                tags=["keep"],
                genres=["keep"],
                description="clear me",
                rating_overall=Decimal("8"),
                locked_fields=["title"],
            )
            db.add(record)
            await db.flush()
            records[kind] = record.id
        await db.commit()
    actor = SimpleNamespace(kind="session")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    app.dependency_overrides[get_current_actor] = lambda: AuthenticatedActor(owner_id, actor.kind)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield SimpleNamespace(client=client, owner_id=owner_id, records=records, actor=actor)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_actor, None)
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == owner_id))
            await db.commit()


@pytest.mark.parametrize("kind", ["movie", "tv", "anime"])
@pytest.mark.parametrize("actor_kind", ["session", "api_key"])
async def test_required_nulls_keep_existing_values_and_nullable_nulls_clear(
    numeric_owner, kind, actor_kind
):
    numeric_owner.actor.kind = actor_kind
    response = await numeric_owner.client.patch(
        f"/api/{kind}/update/{numeric_owner.records[kind]}",
        json={
            "title": None,
            "sort_title": None,
            "status": None,
            "favorite": None,
            "rewatches": None,
            "tags": None,
            "genres": None,
            "provider_ids": None,
            "features": None,
            "studios": None,
            "countries": None,
            "languages": None,
            "description": None,
            "rating_overall": None,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == "Protected title" and data["sort_title"] == "Existing sort"
    assert data["favorite"] is True and data["rewatches"] == 2
    assert data["tags"] == ["keep"] and data["genres"] == ["keep"]
    assert data["provider_ids"] == {} and data["features"] == []
    assert data["status"] is not None
    assert data["description"] is None and data["rating_overall"] is None
    assert "title" in data["locked_fields"]


@pytest.mark.parametrize("kind", ["movie", "tv", "anime"])
async def test_real_zero_and_false_are_applied_and_missing_fields_stay_unchanged(
    numeric_owner, kind
):
    response = await numeric_owner.client.patch(
        f"/api/{kind}/update/{numeric_owner.records[kind]}",
        json={"favorite": False, "rewatches": 0, "rating_overall": 0, "tags": []},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["favorite"] is False and data["rewatches"] == 0
    assert Decimal(str(data["rating_overall"])) == 0 and data["tags"] == []
    assert data["description"] == "clear me" and data["title"] == "Protected title"


@pytest.mark.parametrize("kind", ["movie", "tv", "anime"])
async def test_null_protection_control_still_requires_a_boolean(numeric_owner, kind):
    response = await numeric_owner.client.patch(
        f"/api/{kind}/update/{numeric_owner.records[kind]}", json={"title_lock": None}
    )
    assert response.status_code == 422


async def test_restore_reports_nonfinite_rating_without_losing_good_entries(numeric_owner):
    async with SessionLocal() as db:
        result = await restore_media(
            db,
            numeric_owner.owner_id,
            {
                "movies": [
                    {
                        "title": "Valid restore",
                        "sort_title": "Valid restore",
                        "rating_overall": "0",
                    },
                    {
                        "title": "Invalid rating",
                        "sort_title": "Invalid rating",
                        "rating_overall": "NaN",
                    },
                    {
                        "title": "Another valid",
                        "sort_title": "Another valid",
                        "rating_overall": "8.25",
                    },
                ]
            },
        )
        assert result["created"]["movies"] == 2 and result["skipped"]["movies"] == 1
        assert any("Invalid rating" in error and "finite" in error for error in result["errors"])
        assert (
            await db.scalar(
                select(Movie.id).where(
                    Movie.user_id == numeric_owner.owner_id, Movie.title == "Invalid rating"
                )
            )
            is None
        )


async def test_invalid_nested_restore_rolls_back_parent_and_keeps_next_entry(numeric_owner):
    async with SessionLocal() as db:
        result = await restore_media(
            db,
            numeric_owner.owner_id,
            {
                "anime": [
                    {
                        "title": "Invalid nested",
                        "sort_title": "invalid nested",
                        "seasons": [
                            {
                                "season_number": 1,
                                "episodes": [{"episode_number": 1, "rating": "Infinity"}],
                            }
                        ],
                    },
                    {"title": "Valid after nested", "sort_title": "valid after nested"},
                ]
            },
        )
        assert result["created"]["anime"] == 1 and result["skipped"]["anime"] == 1
        assert any("finite" in error for error in result["errors"])
        assert (
            await db.scalar(
                select(Anime.id).where(
                    Anime.user_id == numeric_owner.owner_id, Anime.title == "Invalid nested"
                )
            )
            is None
        )
