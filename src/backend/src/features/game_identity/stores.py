"""One name per store, whoever reported it.

Steam syncs say "steam", Playnite says "Steam" or "Epic", and the Epic plugin labels its games
"Epic Games". Comparing those strings raw made the same store look like two, so Playnite's Epic
copy and the plugin's Epic copy of one game were offered as copies from different stores instead
of the same copy twice. Keep this list in step with the frontend's `utils/gameCopies.ts`."""

_ALIASES: dict[str, str] = {
    "epic": "epic",
    "epic games": "epic",
    "epic games store": "epic",
    "epicgames": "epic",
    "epic-games": "epic",
    "gog": "gog",
    "gog galaxy": "gog",
    "xbox": "xbox",
    "xbox game pass": "xbox",
    "microsoft store": "microsoft store",
    "ea app": "ea app",
    "origin": "ea app",
    "ubisoft connect": "ubisoft connect",
    "uplay": "ubisoft connect",
    "psn": "playstation",
    "playstation": "playstation",
    "amazon games": "amazon",
    "manual": "manual",
    "none": "manual",
}


def store_key(source: str | None) -> str:
    """A comparable store name; empty when the source is unknown."""
    key = " ".join((source or "").split()).casefold()
    return _ALIASES.get(key, key)
