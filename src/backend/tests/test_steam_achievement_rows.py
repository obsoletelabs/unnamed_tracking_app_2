"""Steam's inconsistent API name casing must not hide unlocked achievements."""

from src.helpers.steam_achievement_rows import steam_achievement_rows


def test_unlocked_names_match_schema_case_insensitively():
    rows = steam_achievement_rows(
        {"ACH_WIN": {"displayName": "Winner", "icon": "color", "icongray": "gray", "hidden": 1}},
        [{"apiname": "ach_win", "achieved": 1, "unlocktime": 123, "description": "You won"}],
    )
    assert rows[0]["external_id"] == "ACH_WIN"
    assert rows[0]["unlocked"] is True
    assert rows[0]["unlocked_at"] == 123
    assert rows[0]["icon_url"] == "color"
    assert rows[0]["description"] == "You won"


def test_missing_player_entry_stays_locked():
    row = steam_achievement_rows({"ACH_WIN": {"icon": "color", "icongray": "gray"}}, [])[0]
    assert row["unlocked"] is False
    assert row["unlocked_at"] is None
    assert row["icon_url"] == "gray"
