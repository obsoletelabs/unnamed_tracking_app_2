"""Provider-reported group play: contract validation, merging and the refresh rules."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.metadata.handler import MetadataHandler
from src.features.provider_play import provider_play_compatibility
from src.plugin_api.metadata_contracts import MetadataPatch, ProviderResponse


def test_patch_accepts_group_play_and_leaves_it_unset_by_default():
    patch = MetadataPatch(players_min=1, players_max=4, crossplay="partial")
    assert (patch.players_min, patch.players_max, patch.crossplay) == (1, 4, "partial")
    empty = MetadataPatch()
    assert empty.players_min is None and empty.players_max is None and empty.crossplay is None
    assert (
        ProviderResponse.model_validate({"metadata": {"players_max": 8}}).metadata.players_max == 8
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"players_min": 5, "players_max": 2},
        {"players_min": 0},
        {"players_max": 33},
        {"players_max": "many"},
        {"crossplay": "maybe"},
        {"crossplay": True},
    ],
)
def test_patch_rejects_invalid_group_play(bad):
    with pytest.raises(ValidationError):
        MetadataPatch(**bad)


def test_merged_metadata_carries_group_play_from_the_best_provider():
    session = SimpleNamespace(providers=[])
    members = [
        SimpleNamespace(priority=2, metadata={"b": MetadataPatch(players_min=1, players_max=2)}),
        SimpleNamespace(
            priority=1, metadata={"a": MetadataPatch(players_max=6, crossplay="yes", title="Game")}
        ),
    ]
    merged = MetadataHandler._metadata(session, members)  # pylint: disable=protected-access
    assert (
        merged["players_max"] == 6 and merged["players_min"] == 1 and merged["crossplay"] == "yes"
    )


def test_provider_values_fill_an_unconfirmed_game():
    match = {"players_min": 1, "players_max": 4, "crossplay": "yes"}
    assert provider_play_compatibility(match, None) == {
        "min_players": 1,
        "max_players": 4,
        "crossplay": True,
        "source": "provider",
    }


@pytest.mark.parametrize("crossplay", ["no", "partial", None])
def test_only_an_explicit_yes_enables_crossplay(crossplay):
    match = {"players_min": 2, "players_max": 2, "crossplay": crossplay}
    assert provider_play_compatibility(match, None)["crossplay"] is False


@pytest.mark.parametrize(
    "match",
    [
        {},
        {"players_max": 4},
        {"players_min": 1},
        {"players_min": 4, "players_max": 2},
        {"players_min": 1, "players_max": 99},
        {"players_min": True, "players_max": 4},
        {"players_min": "1", "players_max": 4},
    ],
)
def test_incomplete_or_invalid_limits_change_nothing(match):
    assert provider_play_compatibility(match, None) is None


def test_user_confirmed_details_are_never_replaced():
    mine = {"min_players": 2, "max_players": 3, "crossplay": False}
    assert provider_play_compatibility({"players_min": 1, "players_max": 8}, mine) is None


def test_earlier_provider_values_are_refreshed_and_keep_the_duration():
    old = {
        "min_players": 1,
        "max_players": 2,
        "crossplay": False,
        "source": "provider",
        "duration_minutes": 90,
    }
    new = provider_play_compatibility({"players_min": 1, "players_max": 4}, old)
    assert new["max_players"] == 4 and new["duration_minutes"] == 90 and new["source"] == "provider"
    assert (
        provider_play_compatibility({"players_min": 1, "players_max": 2}, old) is None
    )  # unchanged
