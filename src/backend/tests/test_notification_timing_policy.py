"""Timing policy: precedence matrix, quiet hours across DST, constrained urgency and trust."""

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.core.notification_timing_schema import DEFAULT_TIMING, normalize_timing
from src.features import notification_timing_policy as timing
from src.features.notification_policy import Trust, effective_trust

NY = ZoneInfo("America/New_York")
DEST = str(uuid4())
OTHER = str(uuid4())


def at(zone, *parts):
    return int(datetime(*parts, tzinfo=zone).timestamp())


def doc(**changes):
    return normalize_timing({**changes})


def facts(**changes):
    base = dict(
        event_type="media.episode.aired",
        purpose="standard",
        category="media",
        destination_id=DEST,
        kind="episode_aired",
    )
    return timing.TimingFacts(**{**base, **changes})


def quiet(start="22:00", end="07:00"):
    return {"enabled": True, "start": start, "end": end}


# Defaults and validation ----------------------------------------------------------------------


def test_default_document_keeps_legacy_event_type_behaviour():
    assert normalize_timing({}) == DEFAULT_TIMING
    held = timing.decide(facts(), DEFAULT_TIMING, now=1_000_000)
    assert (held.action, held.aggregation.strategy) == ("aggregate", "coalesce")
    assert timing.decide(facts(event_type="game.price.threshold_hit"), None, now=5).action == (
        "deliver"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"timezone": "Mars/Base"},
        {"timezone": "localtime"},
        {"timezone": "../etc/passwd"},
        {"quiet_hours": {"enabled": True, "start": "22:00", "end": "22:00"}},
        {"quiet_hours": {"enabled": True, "start": "25:00", "end": "07:00"}},
        {"urgent_threshold": "always"},
        {"plugin_urgent_bypass": "yes"},
        {"digest_times": []},
        {"digest_times": ["8am"]},
        {"unknown": 1},
        {"rules": [{"category": "bank"}]},
        {"rules": [{"mode": "teleport"}]},
        {"rules": [{"destination_id": "not-a-uuid"}]},
        {"rules": [{"trust": 2}]},
        {"rules": [{"category": "media"}, {"category": "media"}]},
        {"rules": [{"urgent": "force"}]},
        {"rules": [{"quiet_hours": {"enabled": True, "start": "9", "end": "10"}}]},
        {"rules": [{"category": "media"}] * 65},
    ],
)
def test_invalid_documents_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_timing(bad)


def test_rules_cannot_assert_trust_or_security_authority():
    for key in ("required_trust", "trust", "purpose", "bypass_security", "grant"):
        with pytest.raises(ValueError):
            normalize_timing({"rules": [{"category": "media", key: 2}]})


# Precedence matrix ----------------------------------------------------------------------------


def rule(category="*", destination=None, **fields):
    return {"category": category, "destination_id": destination, **fields}


def test_precedence_is_category_plus_destination_then_destination_then_category_then_default():
    rules = [
        rule(mode="digest"),
        rule("media", mode="coalesce"),
        rule(destination=DEST, mode="immediate"),
        rule("media", DEST, mode="mute"),
    ]
    now = at(UTC, 2026, 6, 1, 12)

    def mode(**changes):
        return timing.decide(facts(**changes), doc(rules=rules), now=now)

    assert mode().action == "mute"  # category + destination wins
    assert mode(category="game", event_type="game.released").mode == "immediate"  # destination
    other = mode(destination_id=OTHER)
    assert (other.mode, other.rule) == ("coalesce", "media+any")  # category
    fallback = mode(destination_id=OTHER, category="game", event_type="game.released")
    assert fallback.mode == "digest" and fallback.rule == "*+any"  # default rule


def test_rule_order_in_the_document_never_changes_the_outcome():
    rules = [rule("media", DEST, mode="immediate"), rule(mode="mute"), rule("media", mode="digest")]
    now = at(UTC, 2026, 6, 1, 12)
    results = {
        timing.decide(facts(), doc(rules=ordering), now=now).mode
        for ordering in (rules, rules[::-1], rules[1:] + rules[:1])
    }
    assert results == {"immediate"}


def test_fields_resolve_independently_from_the_most_specific_rule_that_sets_them():
    rules = [
        rule("media", DEST, mode="inherit", quiet_hours={"enabled": False}),
        rule("media", mode="coalesce", quiet_hours=quiet("00:00", "23:59")),
        rule(mode="digest"),
    ]
    now = at(UTC, 2026, 6, 1, 12)
    decision = timing.decide(facts(), doc(rules=rules), now=now)
    assert decision.mode == "coalesce"  # mode from the category rule
    assert decision.action == "aggregate"  # quiet hours disabled by the specific rule


def test_rule_quiet_hours_override_the_account_default():
    account = doc(
        timezone="UTC", quiet_hours=quiet(), rules=[rule("game", quiet_hours={"enabled": False})]
    )
    night = at(UTC, 2026, 6, 1, 23)
    held = timing.decide(
        facts(event_type="x.y", category="plugin", kind="plugin"), account, now=night
    )
    assert held.action == "hold"
    free = timing.decide(
        facts(event_type="game.released", category="game", kind="game_released"),
        account,
        now=night,
    )
    assert free.action != "hold"


def test_mute_wins_over_urgency_and_leaves_no_schedule():
    rules = [rule("media", mode="mute")]
    decision = timing.decide(
        facts(route_urgency="critical", severity="critical"), doc(rules=rules), now=1
    )
    assert decision.action == "mute" and decision.release_at is None


@pytest.mark.parametrize("purpose", ["security", "recovery", "verification", "invitation", "test"])
def test_non_standard_purposes_ignore_every_user_rule(purpose):
    harsh = doc(timezone="UTC", quiet_hours=quiet("00:00", "23:59"), rules=[rule(mode="mute")])
    decision = timing.decide(facts(purpose=purpose), harsh, now=at(UTC, 2026, 1, 1, 3))
    assert decision.action == "deliver" and decision.reasons[0].startswith("immune")


def test_inbox_is_never_delayed_or_muted():
    harsh = doc(timezone="UTC", quiet_hours=quiet("00:00", "23:59"), rules=[rule(mode="mute")])
    assert timing.decide(facts(inbox=True), harsh, now=at(UTC, 2026, 1, 1, 3)).action == "deliver"


def test_modes_map_to_aggregation_strategies():
    now = at(UTC, 2026, 6, 1, 12)

    def decide(mode, **changes):
        return timing.decide(
            facts(**changes), doc(timezone="UTC", rules=[rule(mode=mode)]), now=now
        )

    assert decide("immediate").action == "deliver"
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    assert decide("inherit", **plain).action == "deliver"
    forced = decide("coalesce", **plain)
    assert (forced.action, forced.aggregation.window_seconds) == ("aggregate", 900)
    digest = decide("digest", **plain)
    assert digest.action == "hold" and digest.release_at == at(UTC, 2026, 6, 2, 8)


def test_server_can_disable_user_timing_without_losing_event_defaults():
    harsh = doc(quiet_hours=quiet("00:00", "23:59"), rules=[rule(mode="mute")])
    off = timing.ServerTimingPolicy(enabled=False)
    decision = timing.decide(facts(), harsh, now=at(UTC, 2026, 1, 1, 3), server=off)
    assert (decision.action, decision.aggregation.strategy) == ("aggregate", "coalesce")


def test_decisions_are_deterministic_and_explained():
    account = doc(
        timezone="Europe/London", quiet_hours=quiet(), rules=[rule("media", mode="digest")]
    )
    now = at(UTC, 2026, 6, 1, 23)
    first, second = (timing.decide(facts(), account, now=now) for _ in range(2))
    assert first == second
    explained = first.explain()
    assert explained["matched_rule"] == "media+any" and explained["reasons"]
    assert explained["release_at"] == first.release_at


# Quiet hours, timezones and DST ---------------------------------------------------------------


def test_overnight_quiet_hours_hold_until_the_local_end():
    account = doc(timezone="America/New_York", quiet_hours=quiet())
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    inside = at(NY, 2026, 6, 1, 23, 30)
    decision = timing.decide(facts(**plain), account, now=inside)
    assert (decision.action, decision.release_at) == ("hold", at(NY, 2026, 6, 2, 7))
    early = timing.decide(facts(**plain), account, now=at(NY, 2026, 6, 2, 6, 59))
    assert early.release_at == at(NY, 2026, 6, 2, 7)
    for outside in (at(NY, 2026, 6, 2, 7), at(NY, 2026, 6, 2, 21, 59)):
        assert timing.decide(facts(**plain), account, now=outside).action == "deliver"
    assert timing.decide(facts(**plain), account, now=at(NY, 2026, 6, 2, 22)).action == "hold"


def test_same_instant_follows_each_users_own_timezone():
    instant = at(UTC, 2026, 6, 1, 3)  # 23:00 in New York, 12:00 in Tokyo
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    ny = timing.decide(
        facts(**plain), doc(timezone="America/New_York", quiet_hours=quiet()), now=instant
    )
    tokyo = timing.decide(
        facts(**plain), doc(timezone="Asia/Tokyo", quiet_hours=quiet()), now=instant
    )
    assert ny.action == "hold" and tokyo.action == "deliver"


def test_daytime_quiet_range_does_not_wrap():
    account = doc(timezone="UTC", quiet_hours=quiet("09:00", "17:00"))
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    assert (
        timing.decide(facts(**plain), account, now=at(UTC, 2026, 6, 1, 8, 59)).action == "deliver"
    )
    held = timing.decide(facts(**plain), account, now=at(UTC, 2026, 6, 1, 9))
    assert held.release_at == at(UTC, 2026, 6, 1, 17)
    assert timing.decide(facts(**plain), account, now=at(UTC, 2026, 6, 1, 17)).action == "deliver"


def test_spring_forward_night_is_one_hour_shorter_but_ends_at_local_seven():
    # 2026-03-08 02:00 -> 03:00 in New York.
    account = doc(timezone="America/New_York", quiet_hours=quiet())
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    before = at(NY, 2026, 3, 7, 23)
    decision = timing.decide(facts(**plain), account, now=before)
    assert decision.release_at == at(NY, 2026, 3, 8, 7)
    assert decision.release_at - before == 7 * 3600  # 8 wall hours, one skipped
    assert timing.decide(facts(**plain), account, now=at(NY, 2026, 3, 8, 7)).action == "deliver"


def test_fall_back_night_is_one_hour_longer_but_ends_at_local_seven():
    # 2026-11-01 02:00 -> 01:00 in New York.
    account = doc(timezone="America/New_York", quiet_hours=quiet())
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    before = at(NY, 2026, 10, 31, 23)
    decision = timing.decide(facts(**plain), account, now=before)
    assert decision.release_at == at(NY, 2026, 11, 1, 7)
    assert decision.release_at - before == 9 * 3600
    # Both occurrences of 01:30 are inside the window and release at the same local seven.
    first = int(datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0).timestamp())
    second = int(datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=1).timestamp())
    assert second - first == 3600
    assert {timing.decide(facts(**plain), account, now=n).release_at for n in (first, second)} == {
        at(NY, 2026, 11, 1, 7)
    }


def test_quiet_end_inside_the_skipped_hour_resolves_to_the_first_instant_after_the_gap():
    account = doc(timezone="America/New_York", quiet_hours=quiet("01:00", "02:30"))
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    now = at(NY, 2026, 3, 8, 1, 10)
    decision = timing.decide(facts(**plain), account, now=now)
    assert decision.release_at == at(NY, 2026, 3, 8, 3)  # 03:00 EDT, when 02:30 would have been
    assert timing.decide(facts(**plain), account, now=decision.release_at).action == "deliver"


def test_quiet_end_inside_the_repeated_hour_releases_at_the_first_matching_instant_after_now():
    account = doc(timezone="America/New_York", quiet_hours=quiet("01:15", "01:45"))
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    first = int(datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0).timestamp())
    second = int(datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=1).timestamp())
    assert timing.decide(facts(**plain), account, now=first).release_at == first + 15 * 60
    assert timing.decide(facts(**plain), account, now=second).release_at == second + 15 * 60


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "Asia/Kathmandu", "Australia/Lord_Howe"])
def test_fractional_offset_zones_use_their_local_wall_clock(zone):
    tz = ZoneInfo(zone)
    account = doc(timezone=zone, quiet_hours=quiet())
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    decision = timing.decide(facts(**plain), account, now=at(tz, 2026, 6, 1, 23))
    assert decision.release_at == at(tz, 2026, 6, 2, 7)


def test_digest_times_pick_the_next_local_time_and_respect_quiet_hours():
    account = doc(
        timezone="Europe/Paris",
        digest_times=["06:00", "18:00"],
        quiet_hours=quiet(),
        rules=[rule(mode="digest")],
    )
    paris = ZoneInfo("Europe/Paris")
    plain = {"event_type": "x.y", "category": "plugin", "kind": "plugin"}
    morning = timing.decide(facts(**plain), account, now=at(paris, 2026, 6, 1, 12))
    assert morning.release_at == at(paris, 2026, 6, 1, 18)  # next digest, outside quiet hours
    night = timing.decide(facts(**plain), account, now=at(paris, 2026, 6, 1, 19))
    # 06:00 is inside quiet hours (until 07:00), so the digest waits for the end of quiet hours.
    assert night.release_at == at(paris, 2026, 6, 2, 7)
    assert "quiet" in " ".join(night.reasons)


# Urgency ----------------------------------------------------------------------------------------


def test_route_urgency_bypasses_quiet_hours_and_aggregation_by_default():
    account = doc(timezone="UTC", quiet_hours=quiet())
    night = at(UTC, 2026, 6, 1, 23)
    decision = timing.decide(facts(route_urgency="critical"), account, now=night)
    assert decision.action == "deliver" and decision.bypassed


def test_critical_host_severity_bypasses_like_a_route_request():
    account = doc(timezone="UTC", quiet_hours=quiet())
    assert timing.decide(facts(severity="critical"), account, now=at(UTC, 2026, 6, 1, 23)).bypassed


def test_user_threshold_off_blocks_every_bypass():
    account = doc(timezone="UTC", quiet_hours=quiet(), urgent_threshold="off")
    decision = timing.decide(facts(route_urgency="critical"), account, now=at(UTC, 2026, 6, 1, 23))
    assert decision.action == "hold" and not decision.bypassed
    assert any(reason.startswith("urgency") for reason in decision.reasons)


def test_rule_can_deny_urgency_for_one_category_or_destination():
    account = doc(timezone="UTC", quiet_hours=quiet(), rules=[rule("media", DEST, urgent="deny")])
    night = at(UTC, 2026, 6, 1, 23)
    assert timing.decide(facts(route_urgency="critical"), account, now=night).action == "hold"
    other = timing.decide(facts(route_urgency="critical", destination_id=OTHER), account, now=night)
    assert other.action == "deliver"


def test_server_policy_overrides_user_urgency_allowance():
    account = doc(timezone="UTC", quiet_hours=quiet(), rules=[rule("media", DEST, urgent="allow")])
    server = timing.ServerTimingPolicy(urgent_bypass=False)
    decision = timing.decide(
        facts(route_urgency="critical"), account, now=at(UTC, 2026, 6, 1, 23), server=server
    )
    assert decision.action == "hold" and not decision.bypassed


def test_plugin_urgency_request_is_ignored_unless_the_user_opted_in():
    night = at(UTC, 2026, 6, 1, 23)
    plugin = {"category": "plugin", "event_type": "demo.note", "plugin_urgency": "critical"}
    default = doc(timezone="UTC", quiet_hours=quiet())
    assert timing.decide(facts(**plugin), default, now=night).action == "hold"
    opted = doc(timezone="UTC", quiet_hours=quiet(), plugin_urgent_bypass=True)
    assert timing.decide(facts(**plugin), opted, now=night).bypassed
    # The opt-in is still bounded by the server and the user's threshold.
    assert not timing.decide(
        facts(**plugin), {**opted, "urgent_threshold": "off"}, now=night
    ).bypassed
    assert not timing.decide(
        facts(**plugin), opted, now=night, server=timing.ServerTimingPolicy(urgent_bypass=False)
    ).bypassed


def test_urgency_does_not_bypass_when_the_aggregation_policy_forbids_it(monkeypatch):
    from src.features import notification_aggregation_policy as aggregation

    strict = aggregation.AggregationPolicy("coalesce", 600, "event_type", urgent_bypass=False)
    monkeypatch.setitem(aggregation.POLICIES, "media.episode.aired", strict)
    held = timing.decide(facts(route_urgency="critical"), doc(timezone="UTC"), now=5)
    assert held.action == "aggregate" and not held.bypassed
    # A user mode that replaces the policy removes the restriction only for that choice.
    free = timing.decide(
        facts(route_urgency="critical"), doc(rules=[rule(mode="immediate")]), now=5
    )
    assert free.action == "deliver"


# Trust separation -------------------------------------------------------------------------------


def test_decision_carries_no_trust_or_disclosure_fields():
    decision = timing.decide(facts(route_urgency="critical"), doc(), now=5)
    assert set(decision.explain()) == {
        "action",
        "mode",
        "release_at",
        "bypassed_by_urgency",
        "matched_rule",
        "reasons",
    }


def test_urgency_never_changes_destination_trust():
    from types import SimpleNamespace

    destination = SimpleNamespace(
        provider_id="core.smtp",
        channel_context="external",
        privacy=Trust.PUBLIC,
        verified_revision=None,
        revision=1,
        verification_method=None,
        verification_revoked_at=None,
    )
    before = effective_trust(destination)
    timing.decide(facts(route_urgency="critical", severity="critical"), doc(), now=5)
    assert effective_trust(destination) == before == Trust.PUBLIC


def test_category_mapping():
    assert timing.category_of("media.episode.aired", None) == "media"
    assert timing.category_of("game.sale.started", None) == "game"
    assert timing.category_of("system.plugin.update", None) == "system"
    assert timing.category_of("plugin.notice", None) == "plugin"
    assert timing.category_of("media.episode.aired", uuid4()) == "plugin"
