"""Schemas for emptying the movie, TV show and anime trash — see
features/trash/media_titles.py."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool


def _confirmed(value: bool) -> bool:
    if value is not True:
        raise ValueError("Permanent deletion must be confirmed with true")
    return value


class MediaTrashEmptyRequest(BaseModel):
    """Permanent deletion needs an explicit JSON `"confirm": true`; anything else
    (missing, false, 1, "true") is refused before anything is deleted."""

    model_config = ConfigDict(strict=True)

    confirm: Annotated[StrictBool, AfterValidator(_confirmed)]
    # The newest deletion time (Unix seconds) the owner saw when confirming.
    # Titles trashed later are kept. Omitted: everything in the trash now.
    deleted_through: int | None = Field(default=None, ge=0, le=2**63 - 1)


class TrashedTitle(BaseModel):
    id: str
    title: str
    deleted_at: int


class MediaTrashEmptyResult(BaseModel):
    """`deleted` titles are gone for good (with any seasons and episodes).
    `failed` titles could not be deleted and are still in the trash, unchanged.
    `remaining` is what is still in the trash up to `deleted_through`, failed
    titles included: repeat the request with the same `deleted_through` while it
    is larger than `failed`."""

    deleted: int
    failed: int
    failed_items: list[TrashedTitle]
    remaining: int
    deleted_through: int
