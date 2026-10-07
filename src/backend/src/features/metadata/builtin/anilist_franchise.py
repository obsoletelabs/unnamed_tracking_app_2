"""Async AniList transport for the shared bounded franchise workflow."""

from __future__ import annotations

from typing import Any

from src.features.metadata.anime.anilist import _RELATIONS_BY_ID_QUERY, _RELATIONS_QUERY
from src.features.metadata.anime.franchise import FranchiseTraversal, run_async
from src.features.metadata.core_http import ProviderFailure, ProviderHttp


class FranchiseGraph:
    # Only the complete traversal is public.
    # pylint: disable=too-few-public-methods
    def __init__(self, work):
        self.http = ProviderHttp(work, "anilist", min_gap=0.7)

    async def _post_graphql(self, query, variables):
        result = await self.http(
            "https://graphql.anilist.co",
            method="POST",
            body={"query": query, "variables": variables},
        )
        if result.get("errors"):
            raise ProviderFailure("unavailable")
        return result

    async def _fetch_relations_node(
        self, *, media_id: int | None = None, search: str | None = None
    ) -> dict[str, Any] | None:
        """One request's worth of a single Media node: its own id/title
        plus its direct relations edges and recommendations — the unit
        the chain walk in `relations_chain_and_branches` is built from."""
        query = _RELATIONS_QUERY if media_id is None else _RELATIONS_BY_ID_QUERY
        identity: dict[str, Any] = {"search": search} if media_id is None else {"id": media_id}
        result = await self._post_graphql(query, identity)
        return result.get("data", {}).get("Media")

    async def relations_chain_and_branches(self, title, anilist_id=None):
        return await run_async(
            FranchiseTraversal(ProviderFailure).relations_chain_and_branches(title, anilist_id),
            self._fetch_relations_node,
        )
