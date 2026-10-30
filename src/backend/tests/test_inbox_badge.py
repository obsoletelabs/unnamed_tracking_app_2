"""The startup upload badge reads recorded owner rows without scanning storage."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from sqlalchemy import delete

from src.api.routes import media
from src.core.auth import get_current_user
from src.database.models.inbox_item import InboxItem
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app


async def test_upload_badge_counts_only_owned_active_records_without_reconciliation(monkeypatch):
    identities = (uuid4(), uuid4())
    async with SessionLocal() as db:
        users = [
            User(
                id=identity,
                username=identity.hex,
                email=f"{identity}@example.test",
                password_hash="unused",
            )
            for identity in identities
        ]
        db.add_all(users)
        await db.commit()
        db.add_all(
            [
                InboxItem(user_id=identities[0], kind="screenshot", filename="active.png"),
                InboxItem(
                    user_id=identities[0], kind="screenshot", filename="deleted.png", deleted_at=1
                ),
                InboxItem(user_id=identities[1], kind="clip", filename="private.mp4"),
            ]
        )
        await db.commit()
        owner = users[0]
    scan = AsyncMock(side_effect=AssertionError("Badge must not reconcile files"))
    monkeypatch.setattr(media, "_sync_inbox_items", scan)
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/media/inbox/count")
        assert response.status_code == 200 and response.json() == {"count": 1}
        scan.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(identities)))
            await db.commit()
