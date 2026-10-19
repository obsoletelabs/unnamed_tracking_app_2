"""Untrusted snapshot framing, recurrence/time identity and reversible overlays."""

from datetime import date

import pytest

from src.features.calendar_ics import CalendarParseError, parse_calendar
from src.features.calendar_recurrence import expand_calendar


def feed(*events: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        + "\r\n".join("BEGIN:VEVENT\r\n" + event + "\r\nEND:VEVENT" for event in events)
        + "\r\nEND:VCALENDAR\r\n"
    ).encode()


def expand(
    data: bytes, start: str = "2026-10-01", end: str = "2026-10-31", timezone="UTC", overlays=None
):
    components, notices = parse_calendar(data)
    entries, warnings = expand_calendar(
        [(str(i), item) for i, item in enumerate(components)],
        overlays or {},
        date.fromisoformat(start),
        date.fromisoformat(end),
        timezone,
    )
    return entries, notices + warnings


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VCALENDAR",
        b"\xff",
        feed("UID:a\r\nDTSTART:bad"),
        feed("DTSTART:20261019T120000Z"),
        feed("UID:a\r\nDTSTART:20261019T120000Z\r\nDTEND:20261018T120000Z"),
    ],
)
def test_partial_and_malformed_feeds_never_become_empty_success(data):
    with pytest.raises(CalendarParseError):
        parse_calendar(data)


def test_duplicate_last_wins_and_plain_text_is_bounded():
    items, warnings = parse_calendar(
        feed(
            "UID:a\r\nDTSTART:20261019T120000Z\r\nSUMMARY:First",
            "UID:a\r\nDTSTART:20261019T120000Z\r\nSUMMARY:<b>Second</b>\r\nDESCRIPTION:line one\\nline two\r\nURL:javascript:alert(1)",
        )
    )
    assert len(items) == 1 and items[0].title == "<b>Second</b>"
    assert items[0].note == "line one\nline two" and items[0].url == ""
    assert warnings == ["duplicate_components"]


def test_folded_lines_date_exclusive_end_and_unknown_zone():
    entries, warnings = expand(
        feed(
            "UID:a\r\nDTSTART;VALUE=DATE:20261019\r\nDTEND;VALUE=DATE:20261021\r\nSUMMARY:Long\r\n  title",
            "UID:b\r\nDTSTART;TZID=Unrecognised/Zone:20261020T123000",
        )
    )
    assert entries[0]["title"] == "Long title"
    assert entries[0]["event_time"] is None and entries[0]["last_date"] == "2026-10-20"
    assert entries[1]["event_time"] == "12:30" and "unknown_timezone" in warnings


def test_monthly_count_exdate_rdate_exception_and_cancellation():
    data = feed(
        "UID:a\r\nDTSTART;VALUE=DATE:20260831\r\nRRULE:FREQ=MONTHLY;COUNT=3\r\nEXDATE;VALUE=DATE:20261031\r\nRDATE;VALUE=DATE:20261015",
        "UID:a\r\nRECURRENCE-ID;VALUE=DATE:20261015\r\nDTSTART;VALUE=DATE:20261016\r\nSUMMARY:Moved",
    )
    entries, _ = expand(data, "2026-08-01", "2027-01-31")
    assert [(e["event_date"], e["recurrence_key"]) for e in entries] == [
        ("2026-08-31", "D:2026-08-31"),
        ("2026-12-31", "D:2026-12-31"),
        ("2026-10-16", "D:2026-10-15"),
    ]
    cancelled, _ = expand(
        data.replace(b"SUMMARY:Moved", b"STATUS:CANCELLED"), "2026-10-01", "2026-10-31"
    )
    assert cancelled == []


def test_weekly_ancient_series_and_utc_conversion():
    entries, warnings = expand(
        feed("UID:a\r\nDTSTART:19700101T230000Z\r\nRRULE:FREQ=DAILY;INTERVAL=2"),
        timezone="Australia/Perth",
    )
    assert len(entries) >= 14 and not warnings
    assert all(e["event_time"] == "07:00" for e in entries)


def test_dst_gap_is_skipped_without_consuming_count_and_overlap_is_stable():
    entries, _ = expand(
        feed("UID:a\r\nDTSTART;TZID=America/New_York:20260307T023000\r\nRRULE:FREQ=DAILY;COUNT=3"),
        "2026-03-01",
        "2026-03-15",
        "America/New_York",
    )
    assert [e["event_date"] for e in entries] == ["2026-03-07", "2026-03-09", "2026-03-10"]
    overlap, _ = expand(
        feed("UID:a\r\nDTSTART;TZID=America/New_York:20261101T013000"),
        "2026-11-01",
        "2026-11-02",
        "UTC",
    )
    assert overlap[0]["event_time"] == "05:30"


def test_duration_across_dst_uses_elapsed_time_and_exclusions_win_over_exceptions():
    entries, _ = expand(
        feed(
            "UID:a\r\nDTSTART;TZID=America/New_York:20260308T013000\r\nDTEND;TZID=America/New_York:20260308T033000"
        ),
        "2026-03-08",
        "2026-03-08",
        "America/New_York",
    )
    assert entries[0]["duration_seconds"] == 3600 and entries[0]["end_time"] == "03:30"
    entries, _ = expand(
        feed(
            "UID:a\r\nDTSTART;VALUE=DATE:20261019\r\nRRULE:FREQ=DAILY;COUNT=2\r\nEXDATE;VALUE=DATE:20261020",
            "UID:a\r\nRECURRENCE-ID;VALUE=DATE:20261020\r\nDTSTART;VALUE=DATE:20261021",
        )
    )
    assert [event["event_date"] for event in entries] == ["2026-10-19"]


def test_series_and_occurrence_overrides_are_separate_and_removal_wins():
    data = feed("UID:a\r\nDTSTART:20261019T120000Z\r\nRRULE:FREQ=DAILY;COUNT=2\r\nSUMMARY:Upstream")
    key = "U:2026-10-19T12:00:00+00:00"
    overlays = {
        ("a", "*"): {"title": "Series", "shift_seconds": 3600},
        ("a", key): {
            "title": "Only one",
            "start": {"kind": "utc", "value": "2026-10-22T15:00:00+00:00"},
        },
    }
    entries, _ = expand(data, overlays=overlays)
    assert [(e["title"], e["event_date"], e["event_time"]) for e in entries] == [
        ("Only one", "2026-10-22", "15:00"),
        ("Series", "2026-10-20", "13:00"),
    ]
    assert entries[0]["recurrence_key"] == key
    entries, _ = expand(data.replace(b"SUMMARY:Upstream", b"STATUS:CANCELLED"), overlays=overlays)
    assert entries == []


def test_excluded_or_changed_upstream_occurrence_cannot_be_resurrected_by_local_move():
    data = feed(
        "UID:a\r\nDTSTART:20261019T120000Z\r\nRRULE:FREQ=DAILY;COUNT=2\r\nEXDATE:20261019T120000Z"
    )
    overlays = {
        ("a", "U:2026-10-19T12:00:00+00:00"): {
            "start": {"kind": "utc", "value": "2027-01-01T12:00:00+00:00"}
        }
    }
    entries, _ = expand(data, "2027-01-01", "2027-01-02", overlays=overlays)
    assert entries == []


def test_unsupported_and_excessive_recurrences_are_visible_with_a_notice():
    entries, warnings = expand(feed("UID:a\r\nDTSTART:20261019T120000Z\r\nRRULE:FREQ=HOURLY"))
    assert len(entries) == 1 and "unsupported_recurrence" in warnings
    entries, warnings = expand(
        feed("UID:a\r\nDTSTART:20261019T120000Z\r\nRRULE:FREQ=DAILY"), "2026-10-01", "2027-11-02"
    )
    assert len(entries) <= 366 and "expansion_limit" in warnings
