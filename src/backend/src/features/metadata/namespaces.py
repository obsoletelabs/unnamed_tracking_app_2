"""Keeps a plugin metadata provider to the identifier namespaces it owns.

A provider may only vouch for its own ids. Without this a plugin could return `steam` or `igdb`
ids and have `identity.same_entity` join its result to another provider's, or have them saved."""

from collections.abc import Collection

from src.plugin_api.metadata_contracts import (
    MetadataCandidate,
    MetadataPatch,
    ProviderResponse,
)


def _own(provider_ids: dict[str, str], allowed: Collection[str]) -> dict[str, str]:
    return {key: value for key, value in provider_ids.items() if key in allowed}


def _candidate(candidate: MetadataCandidate, allowed: Collection[str]) -> MetadataCandidate:
    if all(key in allowed for key in candidate.provider_ids):
        return candidate
    return candidate.model_copy(update={"provider_ids": _own(candidate.provider_ids, allowed)})


def _patch(patch: MetadataPatch, allowed: Collection[str]) -> MetadataPatch:
    updates: dict = {}
    if any(key not in allowed for key in patch.provider_ids):
        updates["provider_ids"] = _own(patch.provider_ids, allowed)
    relations = tuple(
        relation.model_copy(update={"candidate": _candidate(relation.candidate, allowed)})
        for relation in patch.relations
    )
    if relations != patch.relations:
        updates["relations"] = relations
    return patch.model_copy(update=updates) if updates else patch


def confine_to_namespaces(response: ProviderResponse, allowed: Collection[str]) -> ProviderResponse:
    """Drop provider ids outside `allowed` from every candidate, relation and patch."""
    updates: dict = {}
    candidates = tuple(_candidate(candidate, allowed) for candidate in response.candidates)
    if candidates != response.candidates:
        updates["candidates"] = candidates
    if response.metadata is not None:
        patch = _patch(response.metadata, allowed)
        if patch is not response.metadata:
            updates["metadata"] = patch
    return response.model_copy(update=updates) if updates else response
