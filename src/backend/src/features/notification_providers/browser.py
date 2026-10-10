"""Generic Web Push through reviewed services; global delivery state remains core-owned."""

import asyncio
import json

import aiohttp
from py_vapid import Vapid
from pywebpush import WebPushException, webpush_async
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.features.notification_browser import (
    BrowserContext,
    BrowserSubscription,
    bound_subscription,
    browser_context,
    retire_browser,
)
from src.features.notification_push_config import PUSH_PROVIDER

from .base import DeliveryResult, NotificationMessage, ProviderDestination
from .eligibility import endpoint_route_enabled, revalidate_delivery_attempt


class PushRedirectDenied(ValueError):
    """A push service redirect cannot move encrypted endpoint credentials elsewhere."""


class PushResponseTooLarge(ValueError):
    """A response cannot make the public transport library buffer unbounded data."""


async def _reject_redirect(_session, _trace_context, _params):
    raise PushRedirectDenied("Browser push redirects are disabled")


async def _bound_response(_session, _trace_context, params):
    # The library consumes text only for logging/errors. Drain at most 4 KiB,
    # without retaining or exposing remote error contents. Public trace hooks run
    # before its response.text() call and before a redirect is followed.
    remaining = 4097
    while remaining:
        chunk = await params.response.content.read(remaining)
        if not chunk:
            return
        remaining -= len(chunk)
    params.response.close()
    raise PushResponseTooLarge("Browser push response exceeded its limit")


async def send_push(
    subscription: BrowserSubscription, context: BrowserContext, envelope: dict
) -> DeliveryResult:
    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(_reject_redirect)
    trace.on_request_end.append(_bound_response)
    try:
        async with aiohttp.ClientSession(
            trust_env=False, timeout=aiohttp.ClientTimeout(total=10), trace_configs=[trace]
        ) as session:
            await webpush_async(
                subscription_info=subscription.model_dump(
                    by_alias=True, exclude={"expiration_time"}
                ),
                data=json.dumps(envelope, separators=(",", ":")),
                # Pass a validated in-memory object so library filename support
                # can never read a host file or generate an implicit key.
                vapid_private_key=Vapid.from_string(context.configuration.private_key),
                vapid_claims={"sub": context.configuration.subject},
                ttl=60,
                timeout=10,
                headers={"Urgency": "normal"},
                aiohttp_session=session,
            )
    except WebPushException as exc:
        status = exc.response.status if exc.response is not None else None
        if status in {404, 410}:
            return DeliveryResult(False, error="push_subscription_expired")
        return DeliveryResult(
            False,
            retryable=status in {408, 425, 429} or (status is not None and status >= 500),
            error="push_rejected",
        )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return DeliveryResult(False, retryable=True, error="push_unavailable")
    except (PushRedirectDenied, PushResponseTooLarge, ValueError):
        return DeliveryResult(False, error="push_response_rejected")
    return DeliveryResult(True)


class BrowserNotificationProvider:
    id = PUSH_PROVIDER
    name = "Browser / PWA push"
    critical_supported = False

    async def lookup_destination(self, db, user, setting):
        del db, user, setting
        return None

    async def lookup_endpoint(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
        endpoint: NotificationDestination,
    ) -> ProviderDestination | None:
        if not endpoint_route_enabled(self.id, user.id, setting, endpoint):
            return None
        context = await browser_context(db)
        if context is None or await bound_subscription(db, endpoint, context) is None:
            return None
        return ProviderDestination.for_endpoint(endpoint, self.name)

    async def deliver(self, db, destination: ProviderDestination, message: NotificationMessage):
        if message.purpose not in {"standard", "test"}:
            return DeliveryResult(False, error="push_sensitive_denied")
        context = await browser_context(db)
        endpoint = await revalidate_delivery_attempt(db, destination, message)
        if context is None or endpoint is None:
            return DeliveryResult(False, error="push_routing_changed")
        subscription = await bound_subscription(db, endpoint, context)
        if subscription is None:
            return DeliveryResult(False, error="push_session_changed")
        result = await send_push(
            subscription,
            context,
            {
                "version": 1,
                "destination": str(endpoint.id),
                "revision": endpoint.revision,
                "generation": context.generation,
            },
        )
        if result.error == "push_subscription_expired":
            await retire_browser(db, endpoint, result.error)
            await db.commit()
        return result
