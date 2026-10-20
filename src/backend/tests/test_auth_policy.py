"""Authentication route aliases cannot bypass the same quota/body policy."""

import pytest

from src.api.auth_policy import authentication_operation
from src.features.auth_throttle import AuthOperation


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("POST", "/api/setup", AuthOperation.SETUP),
        ("POST", "/api/auth/login/", AuthOperation.LOGIN),
        ("PATCH", "/api/auth/me", AuthOperation.ACCOUNT_UPDATE),
        ("POST", "/api/auth/api-keys", AuthOperation.API_KEY_CREATE),
        ("POST", "/api/auth/users/member/password", AuthOperation.ACCOUNT_ADMIN),
        ("POST", "/api/auth/user-invitations/link/resend", AuthOperation.ACCOUNT_ADMIN),
        ("GET", "/api/auth/oidc/login", AuthOperation.OIDC_START),
        ("GET", "/api/auth/oidc/login/provider", AuthOperation.OIDC_START),
        ("HEAD", "/api/auth/oidc/callback/provider", AuthOperation.OIDC_CALLBACK),
        ("POST", "/api/auth/password-reset/request", AuthOperation.RECOVERY_REQUEST),
        ("POST", "/api/auth/password-reset/complete", AuthOperation.RECOVERY_COMPLETE),
        ("POST", "/api/auth/user-invites/accept", AuthOperation.INVITATION_ACCEPT),
        ("POST", "/api/delegation/v1/desktop/playnite/requests", AuthOperation.DELEGATION),
        ("POST", "/api/delegation/v1/requests/id/decision", AuthOperation.DELEGATION),
        ("POST", "/api/delegation/v1/requests/id/exchange", AuthOperation.DELEGATION_POLL),
        ("POST", "/api/public-apps/example.app/sessions", AuthOperation.PUBLIC_SESSION),
        ("POST", "/api/settings/steam/login/begin", AuthOperation.STEAM_LOGIN),
        ("POST", "/api/settings/steam/login/poll", AuthOperation.STEAM_POLL),
        ("GET", "/api/settings/steam/openid/callback", AuthOperation.STEAM_LOGIN),
        ("GET", "/api/auth/me", None),
        ("POST", "/api/game/id/documents", None),
    ],
)
def test_route_and_mounted_route_have_identical_policy(method, path, operation):
    assert authentication_operation(method, path) == operation
    assert authentication_operation(method, "/tracker" + path, "/tracker") == operation
