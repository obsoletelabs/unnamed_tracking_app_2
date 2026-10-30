"""Plugin-supplied metadata must not rewrite identities or exceed the stored column sizes."""

from types import SimpleNamespace

from src.api.routes.game_metadata import _game_links, _release_date
from src.features.game_identity.provider_ids import merge_provider_ids
from src.features.metadata.column_limits import fit_response
from src.features.metadata.identity import same_entity
from src.features.metadata.namespaces import confine_to_namespaces
from src.plugin_api.media_enrichment import _apply_metadata
from src.plugin_api.metadata_contracts import (
    MetadataCandidate,
    MetadataLink,
    MetadataPatch,
    ProviderResponse,
)


def _candidate(provider_ids, provider="plugin.books", external_id="b1"):
    return MetadataCandidate(
        title="Dune", external_id=external_id, provider=provider, provider_ids=provider_ids
    )


def test_a_plugin_keeps_only_the_namespaces_it_owns():
    response = ProviderResponse(
        candidates=(_candidate({"plugin.books": "b1", "steam": "10", "igdb": "7"}),),
        metadata=MetadataPatch(provider_ids={"plugin.books": "b1", "steam": "10"}),
    )
    confined = confine_to_namespaces(response, {"plugin.books"})
    assert confined.candidates[0].provider_ids == {"plugin.books": "b1"}
    assert confined.metadata.provider_ids == {"plugin.books": "b1"}


def test_a_plugin_cannot_join_another_providers_result_through_a_forged_id():
    steam = _candidate({}, provider="core.steam", external_id="10")
    forged = _candidate({"core.steam": "10"}, provider="plugin.books", external_id="b1")
    assert same_entity(steam, forged)
    confined = confine_to_namespaces(ProviderResponse(candidates=(forged,)), {"plugin.books"})
    assert not same_entity(steam, confined.candidates[0])


def test_relation_candidates_are_confined_too():
    patch = MetadataPatch.model_validate(
        {
            "relations": [
                {
                    "candidate": {
                        "title": "Sequel",
                        "external_id": "s2",
                        "provider": "plugin.books",
                        "provider_ids": {"steam": "99"},
                    },
                    "relation": "sequel",
                }
            ]
        }
    )
    confined = confine_to_namespaces(ProviderResponse(metadata=patch), {"plugin.books"})
    assert confined.metadata.relations[0].candidate.provider_ids == {}


def test_a_response_inside_its_namespace_is_returned_unchanged():
    response = ProviderResponse(candidates=(_candidate({"plugin.books": "b1"}),))
    assert confine_to_namespaces(response, {"plugin.books"}) is response


def test_saved_ids_win_over_a_sync():
    merged, replaced = merge_provider_ids({"epic": "old"}, {"epic": "new", "gog": "5"})
    assert merged == {"epic": "old", "gog": "5"}
    assert replaced == ["epic"]


def _media(provider_ids, locked=()):
    return SimpleNamespace(
        provider_ids=provider_ids, locked_fields=list(locked), rating_overall=None
    )


def _item(provider_ids):
    return SimpleNamespace(
        metadata={}, media_type="movie", runtime_minutes=None, provider_ids=provider_ids
    )


def test_enrichment_does_not_overwrite_a_saved_id():
    media = _media({"imdb": "tt1"})
    _apply_metadata(media, _item({"imdb": "tt9", "tmdb": "5"}))
    assert media.provider_ids == {"imdb": "tt1", "tmdb": "5"}


def test_enrichment_honours_the_provider_ids_lock():
    media = _media({"imdb": "tt1"}, locked=["provider_ids"])
    _apply_metadata(media, _item({"tmdb": "5"}))
    assert media.provider_ids == {"imdb": "tt1"}


def test_oversized_values_are_clipped_to_the_stored_columns():
    patch = MetadataPatch(
        title="t" * 512,
        format="f" * 64,
        age_rating="a" * 64,
        time_to_beat_hours=100_000,
        links=(
            MetadataLink(label="l" * 128, url="https://example.test/ok"),
            MetadataLink(label="long", url="https://example.test/" + "a" * 2100),
        ),
    )
    fitted = fit_response(ProviderResponse(metadata=patch)).metadata
    assert len(fitted.title) == 500
    assert len(fitted.format) == 20
    assert len(fitted.age_rating) == 20
    assert fitted.time_to_beat_hours is None
    assert [len(link.label) for link in fitted.links] == [50]


def test_game_links_become_rows_and_bad_ones_are_dropped():
    rows = _game_links(
        [
            {"label": "Store", "url": "https://example.test/g"},
            {"label": "", "url": "https://example.test/x"},
            "not a link",
        ]
    )
    assert [(row.label, row.url) for row in rows] == [("Store", "https://example.test/g")]
    assert _game_links([]) is None


def test_release_date_is_parsed_and_garbage_ignored():
    assert _release_date("2020-05-17").isoformat() == "2020-05-17"
    assert _release_date("soon") is None
    assert _release_date(None) is None
