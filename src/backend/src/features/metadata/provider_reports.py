"""What each registered provider itself said about a result (Plugin API 1.1.20).

A record merges every source's metadata, so a value in it cannot be traced back to the provider
that reported it. An identity verdict and a game relation both belong to one provider: a refresh
follows a redirect only in that provider's namespaces (C4.4 rule 2), and a relation's target is
named in that provider's namespace (C6.4). These helpers read them per provider before the merge.

A provider whose ``identity.match`` is ``ambiguous`` named several possible games, so nothing it
reported may be applied to the game (C4.1: never auto-assign ``ambiguous``). Its patch is left
out of the merged record (``handler._metadata``) and its relations out of ingestion; its id
claims are dropped by ``refresh_match``. A ``conflict`` provider's relations are left out too
(the refresh applies nothing then, but a relation must never come from a disputed record).

`provider_values` keeps what each provider reported for the fields a person can prefer a provider
for, also values that lost the merge, so a field preference can choose them (PHASE5 D2).
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from src.plugin_api.base_contracts import parse_semver
from src.plugin_api.metadata_contracts import RELEASES_CONTRACT, MetadataPatch
from src.plugin_api.vocab import RELATION_TYPES

# a refresh considers at most this many relations from one result
MAX_RELATIONS = 64
# identity verdicts whose patch is not applied to a game (C4.1)
UNASSIGNABLE_MATCHES = frozenset({"ambiguous"})
# identity verdicts whose relations are not ingested
UNRELATABLE_MATCHES = frozenset({"ambiguous", "conflict"})
# only work-level relations are game relations; chains, branches and recommendations are not
GAME_RELATION_GROUP = "related"
# Patch fields whose per-provider values reach a refresh (`provider_values`), so a field
# preference can pick a provider whose value lost the merge. Displayable game metadata only;
# never ids, scores, links, custom values or anything a provider marks hidden.
PREFERABLE_FIELDS = frozenset(
    {
        "age_rating",
        "description",
        "developer",
        "features",
        "first_release",
        "publisher",
        "release_date",
        "series",
        "tags",
    }
)


def is_unassignable(patch: MetadataPatch) -> bool:
    """Whether the provider said this lookup named several possible games (C4.1)."""
    return patch.identity is not None and patch.identity.match in UNASSIGNABLE_MATCHES


def reports_game_relations(api_contract: str | None) -> bool:
    """Game relations have a defined meaning only from Plugin API 1.1.20 on (C6.4).

    ``None`` is a host-owned provider (core providers send no vocabulary relations). A plugin
    declaring an older contract may send a vocabulary string such as ``dlc_of`` whose direction
    was never defined for it, so its relations are not ingested; an unreadable version is
    treated as older."""
    if api_contract is None:
        return True
    try:
        return parse_semver(api_contract) >= RELEASES_CONTRACT
    except ValueError:
        return False


# This structural contract describes a data field, not an object with operations.
class _Source(Protocol):  # pylint: disable=too-few-public-methods
    metadata: dict[str, MetadataPatch]


def _patches(
    sources: Mapping[str, _Source], group: Iterable[str], registered: Iterable[str]
) -> Iterable[tuple[str, MetadataPatch]]:
    allowed = set(registered)
    for key in group:
        for provider_id, patch in sources[key].metadata.items():
            if provider_id in allowed:
                yield provider_id, patch


class _Named(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...


def source_labels(providers: Iterable[_Named]) -> dict[str, str]:
    """provider id -> the label its values are recorded and preferred under: the display
    name, or ``name (id)`` when two providers share it (two plugins both called "RAWG")."""
    providers = list(providers)
    names = Counter(provider.name for provider in providers)
    return {p.id: p.name if names[p.name] == 1 else f"{p.name} ({p.id})" for p in providers}


def provider_values(
    sources: Mapping[str, _Source], group: Iterable[str], labels: Mapping[str, str]
) -> dict[str, dict[str, Any]]:
    """label -> {field: value} each registered provider (`labels`, provider id -> label)
    reported for this result, limited to `PREFERABLE_FIELDS`, including values that lost the
    merge. A provider whose identity verdict is ``ambiguous`` reported nothing for this game."""
    out: dict[str, dict[str, Any]] = {}
    for provider_id, patch in _patches(sources, group, labels):
        if is_unassignable(patch):
            continue
        dumped = patch.model_dump(mode="json", include=set(PREFERABLE_FIELDS))
        for name, value in dumped.items():
            if value is not None and value not in ("", [], {}):
                out.setdefault(labels[provider_id], {}).setdefault(name, value)
    return out


def provider_identities(
    sources: Mapping[str, _Source], group: Iterable[str], registered: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """The identity verdict each registered provider gave this result."""
    return {
        provider_id: patch.identity.model_dump(mode="json")
        for provider_id, patch in _patches(sources, group, registered)
        if patch.identity is not None
    }


def provider_relations(
    sources: Mapping[str, _Source],
    group: Iterable[str],
    namespaces: Mapping[str, str | None],
    contracts: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Game relations in the shared vocabulary, each with the ids that name its target.

    ``namespaces`` maps each registered provider to its declared identifier namespace. A target
    is named by the candidate's ``provider_ids`` (already confined to the provider's own
    namespaces) and, when the candidate is the provider's own record, by its ``external_id``
    in the provider's namespace. Relation types outside the vocabulary (anime and film
    relations) and entries outside the ``related`` group (chains, branches, recommendations)
    are not game relations and are left out, as is everything from a provider that declared a
    contract older than 1.1.20 (``contracts``, provider id to declared version) or whose
    identity verdict is ``ambiguous`` or ``conflict``."""
    contracts = contracts or {}
    found: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    for provider_id, patch in _patches(sources, group, namespaces):
        if not reports_game_relations(contracts.get(provider_id)):
            continue
        if patch.identity is not None and patch.identity.match in UNRELATABLE_MATCHES:
            continue
        own = namespaces[provider_id] or provider_id
        for relation in patch.relations:
            if relation.relation not in RELATION_TYPES or relation.group != GAME_RELATION_GROUP:
                continue
            candidate = relation.candidate
            targets = dict(candidate.provider_ids)
            if candidate.provider in {provider_id, own}:
                targets.setdefault(own, candidate.external_id)
            if not targets:
                continue
            key = (relation.relation, tuple(sorted(targets.items())))
            found.setdefault(
                key, {"kind": relation.relation, "targets": targets, "provider": provider_id}
            )
            if len(found) >= MAX_RELATIONS:
                return list(found.values())
    return list(found.values())
