"""Stable colors shared by generated game and collection artwork."""

from uuid import UUID

_PALETTES = (
    ("#16132a", "#3d2a68", "#9b87f5"),
    ("#102326", "#1f5960", "#65d6c7"),
    ("#26151b", "#633044", "#ef8aa8"),
    ("#201a12", "#66502b", "#e3bd69"),
    ("#151c2b", "#294a78", "#76a8ee"),
    ("#1b1722", "#4d315e", "#cf8fe8"),
)


def cover_palette(seed: UUID | str) -> tuple[str, str, str]:
    """Use the same FNV hash and palette as GameArtwork's immediate placeholder."""
    value = 2166136261
    encoded = str(seed).encode("utf-16-le")
    for offset in range(0, len(encoded), 2):
        character = int.from_bytes(encoded[offset : offset + 2], "little")
        value = ((value ^ character) * 16777619) & 0xFFFFFFFF
    return _PALETTES[value % len(_PALETTES)]
