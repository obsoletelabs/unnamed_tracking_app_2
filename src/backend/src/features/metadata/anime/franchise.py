"""One bounded franchise workflow shared by synchronous and asynchronous clients."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from .anilist_relations import RELATION_LABELS, collect_branches, node_to_dict, topological_order

_MAX_CHAIN_HOPS = 8
_CHAIN_RELATION_TYPES = {"PREQUEL", "SEQUEL"}
_MAX_BRANCH_CHAIN_EXPANSIONS = 4
_SHORT_FORMATS = {"MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC"}
_ROOT_FORMATS = {"TV", "TV_SHORT"}
_ROOT_RELATION_TYPES = ("PARENT", "ALTERNATIVE", "SIDE_STORY", "SPIN_OFF")
RELATIONS_CACHE_VERSION = 2


class FranchiseTraversal:
    # Only the complete traversal is public; yielded lookups supply its transport.
    # pylint: disable=too-few-public-methods
    def __init__(self, error_type):
        self.error_type = error_type

    def _fetch_relations_node(self, *, media_id: int | None = None, search: str | None = None):
        return (yield {"media_id": media_id, "search": search})

    def _walk_chain(self, nodes: dict[int, dict[str, Any]], chain_ids: list[int], anchor_id: int):
        """Extends `chain_ids`/`nodes` in place, following PREQUEL edges
        backward and SEQUEL edges forward from the anchor, up to
        `_MAX_CHAIN_HOPS` each way, guarded against cycles."""

        def _walk_one_direction(relation_type: str, prepend: bool):
            current_id = anchor_id
            for _ in range(_MAX_CHAIN_HOPS):
                edges = (nodes[current_id].get("relations") or {}).get("edges") or []
                edge = next((e for e in edges if e.get("relationType") == relation_type), None)
                if not edge or not edge.get("node"):
                    break
                next_id = edge["node"]["id"]
                if next_id in nodes:
                    break
                try:
                    next_node = yield from self._fetch_relations_node(media_id=next_id)
                except self.error_type:
                    break
                if not next_node:
                    break
                nodes[next_id] = next_node
                if prepend:
                    chain_ids.insert(0, next_id)
                else:
                    chain_ids.append(next_id)
                current_id = next_id

        yield from _walk_one_direction("PREQUEL", prepend=True)
        yield from _walk_one_direction("SEQUEL", prepend=False)

    def relations_chain_and_branches(self, title: str, anilist_id: str | None = None):
        """The full prequel/sequel chain this entry belongs to — walked
        via PREQUEL/SEQUEL edges in both directions, not just the anchor's
        own direct relations — plus every other relation type (adaptation,
        side story, source manga/novel, etc.)
        attached to whichever chain entry it's actually connected to.
        A season otherwise only ever lists its immediate neighbor, which
        reads as missing entries for any franchise 3+ seasons deep."""
        opened = yield from self._fetch_relations_node(
            media_id=int(anilist_id) if anilist_id else None, search=None if anilist_id else title
        )
        if not opened:
            return {
                "chain": [],
                "branches": [],
                "recommendations": [],
                "version": RELATIONS_CACHE_VERSION,
            }
        current_id = opened["id"]
        anchor = (yield from self._find_franchise_root(opened)) or opened
        nodes: dict[int, dict[str, Any]] = {anchor["id"]: anchor}
        chain_ids: list[int] = [anchor["id"]]
        yield from self._walk_chain(nodes, chain_ids, anchor["id"])
        chain = [
            {**node_to_dict(nodes[node_id]), "is_current": node_id == current_id}
            for node_id in chain_ids
        ]
        recommendations = [
            node_to_dict(rec["mediaRecommendation"])
            for rec in (anchor.get("recommendations") or {}).get("nodes") or []
            if rec.get("mediaRecommendation")
        ]
        branches = yield from self._order_related_branches(
            collect_branches(nodes, anchor["id"], chain_ids)
        )
        seen_ids = set(chain_ids) | {b["id"] for b in branches}
        branches.extend((yield from self._expand_branch_chains(branches, seen_ids)))
        for b in branches:
            b["is_current"] = b["id"] == current_id
        if current_id not in seen_ids and opened["id"] != anchor["id"]:
            branches.append(
                {
                    "anchor_id": anchor["id"],
                    "anchor_kind": "show",
                    "relation_label": "Related",
                    "is_current": True,
                    **node_to_dict(opened),
                }
            )
        return {
            "chain": chain,
            "branches": branches,
            "recommendations": recommendations,
            "version": RELATIONS_CACHE_VERSION,
        }

    def _find_franchise_root(self, opened: dict[str, Any]):
        """For a movie/OVA/special, the parent TV series it hangs off (one
        hop, which is all AniList's PARENT/ALTERNATIVE links need), fetched
        with its own relations. None when the entry is already a series,
        has no such link, or the parent can't be fetched."""
        if (opened.get("format") or "").upper() not in _SHORT_FORMATS:
            return None
        edges = (opened.get("relations") or {}).get("edges") or []
        for wanted in _ROOT_RELATION_TYPES:
            for edge in edges:
                node = edge.get("node")
                if edge.get("relationType") != wanted or not node:
                    continue
                if (node.get("format") or "").upper() not in _ROOT_FORMATS:
                    continue
                try:
                    return (yield from self._fetch_relations_node(media_id=node["id"]))
                except self.error_type:
                    return None
        return None

    def _expand_branch_chains(self, branches: list[dict[str, Any]], seen_ids: set[int]):
        """A top-level branch can have its own prequel/sequel that's invisible from the anchor's
        own relations — e.g. Bleach's "BURN THE WITCH" ONA has its own prequel special ("BURN
        THE WITCH #0.8") that's only a relation of the ONA itself, one hop past what
        `collect_branches` ever looks at (the anchor's direct relations only). One extra fetch
        per still-top-level branch, pulling in any PREQUEL/SEQUEL neighbor not already known and
        nesting it under that branch (`anchor_kind: "branch"`) — the same nested-branch shape
        `_order_related_branches` already produces for a duology it detects. Single hop only
        (not a full walk), restricted to short-form formats that actually tend to have their own
        mini-chain (OVA/ONA/Special/One Shot — a movie or source manga essentially never does),
        and capped to a handful of extra fetches total — this is a real AniList request per
        branch checked, and a franchise with a dozen+ branches would otherwise turn one
        relations fetch into a dozen+ more, which is a bad trade for a detail few branches
        actually have."""
        candidates = [
            b
            for b in branches
            if b["anchor_kind"] == "show"
            and (b.get("format") or "").lower() in {"ova", "ona", "special", "one shot"}
        ]
        extra: list[dict[str, Any]] = []
        checked = 0
        for branch in candidates:
            if checked >= _MAX_BRANCH_CHAIN_EXPANSIONS:
                break
            checked += 1
            try:
                node = yield from self._fetch_relations_node(media_id=branch["id"])
            except self.error_type:
                continue
            if not node:
                continue
            for edge in (node.get("relations") or {}).get("edges") or []:
                rtype = edge.get("relationType")
                target = edge.get("node")
                if rtype not in _CHAIN_RELATION_TYPES or not target:
                    continue
                target_id = target["id"]
                if target_id in seen_ids:
                    continue
                seen_ids.add(target_id)
                extra.append(
                    {
                        "anchor_id": branch["id"],
                        "anchor_kind": "branch",
                        "relation_label": RELATION_LABELS.get(rtype, "Related"),
                        **node_to_dict(target),
                    }
                )
        return extra

    def _fetch_group_prequel_pointers(self, group: list[dict[str, Any]], ids: set[int]):
        """Fetches each group member's own relations and returns
        {member_id: its_prequel_id} restricted to prequels that are
        themselves in the group (an outside prequel isn't useful for
        ordering the group)."""
        prequel_of: dict[int, int] = {}
        for b in group:
            try:
                node = yield from self._fetch_relations_node(media_id=b["id"])
            except self.error_type:
                continue
            if not node:
                continue
            for edge in (node.get("relations") or {}).get("edges") or []:
                if edge.get("relationType") != "PREQUEL":
                    continue
                target = (edge.get("node") or {}).get("id")
                if isinstance(target, int) and target in ids:
                    prequel_of[b["id"]] = target
        return prequel_of

    def _order_related_branches(self, branches: list[dict[str, Any]]):
        """A branch group sharing the same anchor and relation label —
        e.g. a two-part movie duology, both tagged ALTERNATIVE to the
        parent show rather than SEQUEL/PREQUEL to it — can still be
        chronologically ordered via their own mutual PREQUEL edges.
        Fetches each 2+-member group once to find that order; every
        member after the first is then reparented onto its immediate
        predecessor (anchor_kind "branch") instead of the show, and
        labeled "Sequel" — a real edge between the siblings themselves,
        matching how the source actually relates them, rather than two
        independent spokes off the show that just happen to sit in the
        right order."""
        groups: dict[tuple[int, str], list[int]] = {}
        for i, b in enumerate(branches):
            groups.setdefault((b["anchor_id"], b["relation_label"]), []).append(i)
        for positions in groups.values():
            if not 2 <= len(positions) <= 4:
                continue
            group = [branches[i] for i in positions]
            ids = {b["id"] for b in group}
            prequel_of = yield from self._fetch_group_prequel_pointers(group, ids)
            order = topological_order(ids, prequel_of)
            if order is None:
                continue
            by_id = {b["id"]: b for b in group}
            for slot, branch_id in zip(sorted(positions), order, strict=True):
                branches[slot] = by_id[branch_id]
            for prev_id, branch_id in pairwise(order):
                child = by_id[branch_id]
                child["anchor_id"] = prev_id
                child["anchor_kind"] = "branch"
                child["relation_label"] = "Sequel"
        return branches


def run_sync(workflow, fetch):
    request = next(workflow)
    while True:
        try:
            try:
                result = fetch(**request)
            except RuntimeError as exc:
                request = workflow.throw(exc)
            else:
                request = workflow.send(result)
        except StopIteration as complete:
            return complete.value


async def run_async(workflow, fetch):
    request = next(workflow)
    while True:
        try:
            try:
                result = await fetch(**request)
            except RuntimeError as exc:
                request = workflow.throw(exc)
            else:
                request = workflow.send(result)
        except StopIteration as complete:
            return complete.value
