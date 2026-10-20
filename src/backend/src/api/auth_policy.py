"""HTTP authentication operations and their deployment-scaled, endpoint-specific quotas."""

import re

from src.core.config import settings
from src.features.auth_throttle import AuthOperation

AUTH_REQUEST_MAX_BYTES = 32 * 1024

_EXACT = {
    ("POST", "/api/setup"): AuthOperation.SETUP,
    ("POST", "/api/auth/login"): AuthOperation.LOGIN,
    ("POST", "/api/auth/password-reset/request"): AuthOperation.RECOVERY_REQUEST,
    ("POST", "/api/auth/password-reset/complete"): AuthOperation.RECOVERY_COMPLETE,
    ("POST", "/api/auth/user-invites/accept"): AuthOperation.INVITATION_ACCEPT,
    ("POST", "/api/auth/api-keys"): AuthOperation.API_KEY_CREATE,
    ("PATCH", "/api/auth/me"): AuthOperation.ACCOUNT_UPDATE,
    ("POST", "/api/auth/users"): AuthOperation.ACCOUNT_ADMIN,
    ("PUT", "/api/auth/password-policy"): AuthOperation.ACCOUNT_ADMIN,
    ("POST", "/api/delegation/v1/requests"): AuthOperation.DELEGATION,
    ("POST", "/api/delegation/v1/clients/register"): AuthOperation.DELEGATION,
    ("POST", "/api/delegation/v1/credential/rotate"): AuthOperation.DELEGATION,
}


_PATTERNS = {
    "GET": (
        (r"/api/auth/oidc/login(?:/[^/]+)?", AuthOperation.OIDC_START),
        (r"/api/auth/oidc/callback(?:/[^/]+)?", AuthOperation.OIDC_CALLBACK),
        (r"/api/settings/steam/openid/callback", AuthOperation.STEAM_LOGIN),
    ),
    "POST": (
        (r"/api/auth/(?:users|user-invitations)/.+", AuthOperation.ACCOUNT_ADMIN),
        (r"/api/delegation/v1/requests/[^/]+/exchange", AuthOperation.DELEGATION_POLL),
        (
            r"/api/delegation/v1/(?:desktop/[^/]+/requests|requests/[^/]+/decision)",
            AuthOperation.DELEGATION,
        ),
        (r"/api/public-apps/[^/]+/sessions", AuthOperation.PUBLIC_SESSION),
        (r"/api/settings/steam/login/poll", AuthOperation.STEAM_POLL),
        (r"/api/settings/steam/(?:login/[^/]+|openid/begin)", AuthOperation.STEAM_LOGIN),
    ),
}


def authentication_operation(method: str, path: str, root_path: str = "") -> AuthOperation | None:
    root_path = root_path.rstrip("/")
    if root_path and path.startswith(root_path + "/"):
        path = path[len(root_path) :]
    if method == "HEAD":
        method = "GET"
    path = path.rstrip("/")
    if operation := _EXACT.get((method, path)):
        return operation
    return next(
        (
            operation
            for pattern, operation in _PATTERNS.get(method, ())
            if re.fullmatch(pattern, path)
        ),
        None,
    )


_CALLER_LIMITS = {
    AuthOperation.SETUP: (10, 3600),
    AuthOperation.OIDC_START: (60, 300),
    AuthOperation.OIDC_CALLBACK: (120, 300),
    AuthOperation.RECOVERY_REQUEST: (30, 3600),
    AuthOperation.RECOVERY_COMPLETE: (30, 900),
    AuthOperation.INVITATION_ACCEPT: (30, 900),
    AuthOperation.ACCOUNT_ADMIN: (120, 3600),
    AuthOperation.ACCOUNT_UPDATE: (120, 3600),
    AuthOperation.API_KEY_CREATE: (60, 3600),
    AuthOperation.DELEGATION: (60, 300),
    AuthOperation.DELEGATION_POLL: (3600, 300),
    AuthOperation.PUBLIC_SESSION: (120, 300),
    AuthOperation.STEAM_LOGIN: (60, 300),
    AuthOperation.STEAM_POLL: (3600, 300),
}

ACTOR_LIMITS = {
    AuthOperation.ACCOUNT_ADMIN: (100, 86400),
    AuthOperation.ACCOUNT_UPDATE: (60, 3600),
    AuthOperation.API_KEY_CREATE: (10, 3600),
    AuthOperation.DELEGATION: (60, 3600),
    AuthOperation.STEAM_LOGIN: (30, 300),
    AuthOperation.STEAM_POLL: (600, 300),
}


def caller_limit(operation: AuthOperation) -> tuple[int, int]:
    if operation == AuthOperation.LOGIN:
        return settings.AUTH_LOGIN_IP_PER_MINUTE, 60
    return _CALLER_LIMITS[operation]
