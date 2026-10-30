"""Slow folder checks must not serialize unrelated asynchronous requests."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.routes.utils import games


@pytest.mark.asyncio
async def test_storage_lookup_keeps_the_event_loop_available_while_checks_run(monkeypatch):
    game = SimpleNamespace(id=uuid4(), user_id=uuid4(), folder_location="Example")
    db = SimpleNamespace(scalar=AsyncMock(return_value=game))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_check(_root, _game):
        entered.set()
        release.wait(2)
        finished.set()

    monkeypatch.setattr(games.game_folders, "ensure_storage_ready", slow_check)
    lookup = asyncio.create_task(
        games._get_game_or_404(game.id, db, game.user_id, for_storage=True)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # This coroutine can continue while the filesystem check is blocked.
        assert not finished.is_set()
        assert not lookup.done()
    finally:
        release.set()
        result = await lookup
    assert result is game
    assert finished.is_set()
