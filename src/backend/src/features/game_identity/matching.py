"""Conservative, explainable evidence for whether two library records are one copy (#99).

Pure: no database access. A title alone is never proof. Every assessment lists
the evidence that matched, what conflicted and what was missing, so a person
can see why a pair was merged or queued. Only one rule may merge automatically
and it needs every relevant field to be present, equal and trusted; everything
else is queued for review or stays separate without a prompt.
"""

# pylint: disable=too-many-instance-attributes,too-many-branches

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unicodedata import normalize

RULE = "same-copy-evidence"
RULE_VERSION = "1"
AUTO_CONFIDENCE = 0.96

_EDITION_WORDS = frozenset(
    {
        "goty",
        "deluxe",
        "definitive",
        "complete",
        "ultimate",
        "remastered",
        "remaster",
        "remake",
        "collectors",
        "gold",
        "enhanced",
        "anniversary",
        "premium",
        "legendary",
        "hd",
        "directors",
    }
)
_WEIGHTS = {
    "title": 0.3,
    "provider_id": 0.3,
    "release_date": 0.15,
    "platform": 0.1,
    "edition": 0.05,
    "playtime": 0.05,
    "last_played": 0.05,
}
_REVIEW_CAP = 0.89


def title_key(title: str) -> str:
    """Ignore cosmetic punctuation without stripping accents, scripts or editions."""
    title = normalize("NFKC", title.replace("™", "").replace("®", ""))
    title = title.replace("©", "").casefold().replace("'", "").replace("’", "")
    return " ".join("".join(char if char.isalnum() else " " for char in title).split())


def edition_tokens(title: str, observed: str | None = None) -> frozenset[str]:
    words = set(title_key(title).split()) & _EDITION_WORDS
    if observed:
        words.add("edition:" + title_key(observed))
    return frozenset(words)


@dataclass(frozen=True)
class Candidate:
    """The comparable facts of one library record."""

    id: Any
    title: str
    source: str | None = None
    platform: str | None = None
    release_date: date | None = None
    external_id: str | None = None
    provider_ids: dict[str, str] = field(default_factory=dict)
    playtime_seconds: int = 0
    last_played_at: int | None = None
    parent_game_id: Any = None
    relationship_type: str | None = None
    observed_edition: str | None = None
    # an available record from a connected source reported it (not a stale or failed pull)
    trusted: bool = False

    @property
    def key(self) -> str:
        return title_key(self.title)

    @property
    def edition(self) -> frozenset[str]:
        return edition_tokens(self.title, self.observed_edition)


@dataclass
class Assessment:
    level: str  # auto | review | low | conflict
    confidence: float
    matched: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def evidence(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "matched": self.matched,
            "conflicts": self.conflicts,
            "missing": self.missing,
        }


def _known_differ(left: Any, right: Any) -> bool:
    return bool(left) and bool(right) and left != right


def _conflicts(a: Candidate, b: Candidate) -> list[str]:
    found: list[str] = []
    if a.parent_game_id or b.parent_game_id:
        found.append("grouped_copy")
    if _known_differ(a.platform, b.platform):
        found.append("platform")
    if _known_differ(a.relationship_type, b.relationship_type):
        found.append("relationship_type")
    if a.release_date and b.release_date and a.release_date.year != b.release_date.year:
        found.append("release_year")
    if any(
        a.provider_ids[k] != b.provider_ids[k]
        for k in a.provider_ids.keys() & b.provider_ids.keys()
    ):
        found.append("provider_id")
    if a.source and a.source == b.source and a.external_id and b.external_id:
        if a.external_id != b.external_id:
            found.append("source_record")
    if a.observed_edition and b.observed_edition and a.edition != b.edition:
        found.append("edition")
    return found


def assess(a: Candidate, b: Candidate) -> Assessment:
    """Classify one pair. Conflicts always win; title-only evidence is `low`."""
    conflicts = _conflicts(a, b)
    if conflicts:
        return Assessment("conflict", 0.0, conflicts=conflicts)
    matched: list[str] = []
    missing: list[str] = []
    title_equal = bool(a.key) and a.key == b.key
    if title_equal:
        matched.append("title")
    shared = sorted(k for k in a.provider_ids.keys() & b.provider_ids.keys() if a.provider_ids[k])
    if shared:
        matched.append("provider_id")
    release_equal = bool(a.release_date) and a.release_date == b.release_date
    if release_equal:
        matched.append("release_date")
    elif not (a.release_date and b.release_date):
        missing.append("release_date")
    platform_equal = bool(a.platform) and a.platform == b.platform
    if platform_equal:
        matched.append("platform")
    elif not (a.platform and b.platform):
        missing.append("platform")
    edition_equal = a.edition == b.edition
    if edition_equal:
        matched.append("edition")
    else:
        missing.append("edition")
    playtime_equal = a.playtime_seconds > 0 and a.playtime_seconds == b.playtime_seconds
    if playtime_equal:
        matched.append("playtime")
    last_equal = a.last_played_at is not None and a.last_played_at == b.last_played_at
    if last_equal:
        matched.append("last_played")
    if not (a.trusted and b.trusted):
        missing.append("trusted_inventory")
    score = round(min(sum(_WEIGHTS[name] for name in matched), _REVIEW_CAP), 3)
    auto = (
        title_equal
        and release_equal
        and platform_equal
        and edition_equal
        and playtime_equal
        and last_equal
        and a.trusted
        and b.trusted
    )
    if auto:
        return Assessment("auto", AUTO_CONFIDENCE, matched, missing=missing)
    corroborated = title_equal and (shared or release_equal or platform_equal)
    if corroborated or shared:
        return Assessment("review", score, matched, missing=missing)
    return Assessment("low", score, matched, missing=missing)
