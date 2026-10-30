"""Fits a plugin's metadata to the sizes the database stores.

The public contract allows longer values than the columns hold, and changing it would be a
versioned change for every plugin. The host therefore clips here, before anything is saved, so
one oversized value cannot fail a refresh for every user of that plugin."""

from src.plugin_api.metadata_contracts import MetadataLink, MetadataPatch, ProviderResponse

TITLE_LENGTH = 500
FORMAT_LENGTH = 20
AGE_RATING_LENGTH = 20
LINK_LABEL_LENGTH = 50
LINK_URL_LENGTH = 2048
MAX_HOURS = 99_999.9


def _clip(value: str | None, length: int) -> str | None:
    return value if value is None else value[:length]


def _links(links: tuple[MetadataLink, ...]) -> tuple[MetadataLink, ...]:
    return tuple(
        link.model_copy(update={"label": link.label[:LINK_LABEL_LENGTH]})
        for link in links
        if len(str(link.url)) <= LINK_URL_LENGTH
    )


def fit_patch(patch: MetadataPatch) -> MetadataPatch:
    """Clip text to the column sizes and drop numbers a column cannot hold."""
    updates: dict = {
        "title": _clip(patch.title, TITLE_LENGTH),
        "format": _clip(patch.format, FORMAT_LENGTH),
        "age_rating": _clip(patch.age_rating, AGE_RATING_LENGTH),
        "links": _links(patch.links),
    }
    if patch.time_to_beat_hours is not None and patch.time_to_beat_hours > MAX_HOURS:
        updates["time_to_beat_hours"] = None
    return patch.model_copy(update=updates)


def fit_response(response: ProviderResponse) -> ProviderResponse:
    if response.metadata is None:
        return response
    return response.model_copy(update={"metadata": fit_patch(response.metadata)})
