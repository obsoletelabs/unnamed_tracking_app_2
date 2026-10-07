"""Conservative identity resolution and deterministic host-owned ranking."""

from __future__ import annotations

from difflib import SequenceMatcher
from uuid import NAMESPACE_URL, uuid5

from src.plugin_api.metadata_contracts import MetadataCandidate


def normalized_title(value: str) -> str:
    """Cheap punctuation-insensitive comparison shared conceptually with local filtering."""
    return "".join(character.casefold() for character in value if character.isalnum())


def identities(candidate: MetadataCandidate) -> dict[str, str]:
    """A provider's own identity always participates alongside declared cross-provider IDs."""
    return {**candidate.provider_ids, candidate.provider: candidate.external_id}


def same_entity(first: MetadataCandidate, second: MetadataCandidate) -> bool:
    """Never merge conflicting IDs, types, years or platform-specific editions."""
    if first.media_type != second.media_type:
        return False
    left, right = identities(first), identities(second)
    shared = set(left) & set(right)
    if any(left[key] != right[key] for key in shared):
        return False
    if shared:
        return True
    if first.year is None or second.year is None or first.year != second.year:
        return False
    if first.platforms and second.platforms and not set(first.platforms) & set(second.platforms):
        return False
    left_titles = {normalized_title(title) for title in (first.title, *first.alternate_titles)}
    right_titles = {normalized_title(title) for title in (second.title, *second.alternate_titles)}
    return bool((left_titles & right_titles) - {""})


def candidate_id(candidate: MetadataCandidate) -> str:
    """Stable identity within a provider, independent of response timing."""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"metadata/{candidate.media_type}/{candidate.provider}/{candidate.external_id}",
        )
    )


def rank_key(query: str, candidate: MetadataCandidate, priority: int, position: int) -> tuple:
    """Exact/alternate matches, similarity, provider priority and stable identity break ties."""
    query_title = normalized_title(query)
    titles = (candidate.title, *candidate.alternate_titles)
    exact = any(normalized_title(title) == query_title for title in titles)
    similarity = max(
        SequenceMatcher(None, query_title, normalized_title(title)).ratio() for title in titles
    )
    query_tokens = set(query.casefold().split())
    title_tokens = set(candidate.title.casefold().split())
    overlap = len(query_tokens & title_tokens) / max(1, len(query_tokens | title_tokens))
    return (
        -int(exact),
        -similarity,
        -overlap,
        priority,
        position,
        normalized_title(candidate.title),
        candidate.year or 0,
        candidate.provider,
        candidate.external_id,
    )
