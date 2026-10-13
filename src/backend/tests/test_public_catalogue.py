"""Catalogue fallback keeps ambiguous titles separate and reuses built-in metadata."""

import pytest

from src.features import public_catalogue


@pytest.mark.parametrize(
    "titles,matched",
    [
        (["SuBnA uTicA2"], True),
        (["Subnautica 2", "Subnautica 2!"], False),
        (["Subnautica", "Subnautica 2 Deluxe Edition"], False),
    ],
)
async def test_catalogue_only_resolves_one_normalized_exact_title(monkeypatch, titles, matched):
    public_catalogue._cache.clear()
    calls = []

    async def search(values):
        assert values["request"]["query"] == "Subnautica 2"
        return {
            "candidates": [
                {"title": title, "provider_ids": {"steam": str(index + 1)}}
                for index, title in enumerate(titles)
            ]
        }

    async def metadata(values):
        calls.append(values)
        return {
            "metadata": {
                "title": "Subnautica 2",
                "provider_ids": {"steam": "1"},
                "features": ["Co-op"],
            }
        }

    monkeypatch.setattr(public_catalogue.steam, "search", search)
    monkeypatch.setattr(public_catalogue.steam, "metadata", metadata)
    info = await public_catalogue.game_info("Subnautica 2")
    assert info["matched"] is matched
    assert len(calls) == int(matched)
    if matched:
        assert (await public_catalogue.game_info("Subnautica 2")) == info
        assert len(calls) == 1


async def test_cover_tries_the_next_builtin_artwork_candidate(monkeypatch):
    monkeypatch.setattr(
        public_catalogue.steam,
        "media",
        lambda values: {
            "assets": [
                {"kind": "key_art", "url": "https://example.test/absent"},
                {"kind": "banner", "url": "https://example.test/present"},
            ]
        },
    )
    calls = []

    def image(url):
        calls.append(url)
        if url.endswith("absent"):
            raise ValueError("not found")
        return {"content": b"fixture", "media_type": "image/jpeg"}

    monkeypatch.setattr(public_catalogue, "public_image_request", image)
    assert (await public_catalogue.artwork("123"))["content"] == b"fixture"
    assert len(calls) == 2
