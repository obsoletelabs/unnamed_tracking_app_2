from __future__ import annotations

import hashlib
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Final, Literal, TypeVar
from uuid import UUID

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth_checks import check_credential
from src.core.config import settings
from src.core.metrics import AUTH_SESSION_TRANSITIONS
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.user import User
from src.database.session import get_db

_SALT_BYTES: Final = 16
_HASH_BYTES: Final = 32
_SCRYPT_MAX_N: Final = 2**17
_SCRYPT_MAX_MEMORY: Final = 256 * 1024 * 1024
_ARGON2_MAX_MEMORY_KIB: Final = 262_144
_ARGON2_MAX_TIME_COST: Final = 10
_ARGON2_MAX_LANES: Final = 8
_DB_DEPENDENCY = Depends(get_db)
SESSION_COOKIE_PREFIX: Final = "session_"
SESSION_TTL_SECONDS: Final = 30 * 24 * 60 * 60
API_KEY_PREFIX: Final = "utk_"
_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])


@dataclass(frozen=True)
class AuthenticatedActor:
    """Credential provenance established by authentication, never by the client."""

    user_id: UUID
    credential_kind: Literal["session", "api_key", "plugin"]

    @property
    def can_manage_protected_metadata(self) -> bool:
        """Only a validated application sign-in session can manage protection."""
        return self.credential_kind == "session"


_password_policy_override: dict[str, int | bool] | None = None


def password_policy() -> dict[str, int | bool]:
    if _password_policy_override is not None:
        return dict(_password_policy_override)
    return {
        "min_length": settings.PASSWORD_MIN_LENGTH,
        "require_uppercase": settings.PASSWORD_REQUIRE_UPPERCASE,
        "require_lowercase": settings.PASSWORD_REQUIRE_LOWERCASE,
        "require_digit": settings.PASSWORD_REQUIRE_DIGIT,
        "require_symbol": settings.PASSWORD_REQUIRE_SYMBOL,
    }


def set_password_policy_override(policy: dict[str, int | bool] | None) -> None:
    # The deployment override is shared by password validation in every request.
    global _password_policy_override  # pylint: disable=global-statement
    _password_policy_override = dict(policy) if policy is not None else None


def validate_password(password: str, policy: dict[str, int | bool] | None = None) -> str:
    """Validate the configured password policy and return the original value."""
    policy = policy if policy is not None else password_policy()
    if len(password) < policy["min_length"]:
        raise ValueError(f"Password must be at least {policy['min_length']} characters long.")
    if policy["require_uppercase"] and not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain an uppercase letter.")
    if policy["require_lowercase"] and not re.search(r"[a-z]", password):
        raise ValueError("Password must contain a lowercase letter.")
    if policy["require_digit"] and not re.search(r"\d", password):
        raise ValueError("Password must contain a number.")
    if policy["require_symbol"] and not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("Password must contain a symbol.")
    return password


USERNAME_MAX_LENGTH = 100
_USERNAME_ALLOWED = re.compile(r"[A-Za-z0-9_.-]+")
_USERNAME_RULE = "Use only ASCII letters, digits, underscores, hyphens and dots."


def _describe_username_character(char: str) -> str:
    if char == " ":
        return "spaces"
    if char.isprintable():
        return f"\u201c{char}\u201d"
    return "control or invisible characters"


def username_rule_error(value: str) -> str | None:
    """Name the first rule a new/renamed username breaks, or None when it is valid.

    The frontend mirrors these rules and messages in ``services/usernameValidation.ts``;
    keep the two in step (``tests/test_username_validation.py`` pins the vectors).
    Existence checks are deliberately not part of this: they stay in the save path.
    """
    username = value.strip()
    if not username:
        return "Enter a username."
    if len(username) > USERNAME_MAX_LENGTH:
        return (
            f"Username is too long: use at most {USERNAME_MAX_LENGTH} characters "
            f"(this one has {len(username)})."
        )
    if not _USERNAME_ALLOWED.fullmatch(username):
        offenders: list[str] = []
        for char in username:
            if _USERNAME_ALLOWED.fullmatch(char):
                continue
            label = _describe_username_character(char)
            if label not in offenders:
                offenders.append(label)
        shown = ", ".join(offenders[:5]) + (", \u2026" if len(offenders) > 5 else "")
        return f"Username cannot contain {shown}. {_USERNAME_RULE}"
    return None


async def username_in_use(
    db: AsyncSession, username: str, *, exclude_user_id: UUID | None = None
) -> bool:
    """Whether another account already has this name, ignoring ASCII letter case.

    New and renamed usernames may not differ from an existing one only by case ("Admin" next to
    "admin"), which would let one person impersonate another. Existing accounts are never
    renamed or locked out: sign-in still matches the stored name exactly, so legacy pairs keep
    working and only the creation paths use this check.
    """
    statement = select(User.id).where(func.lower(User.username) == username.strip().lower())
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    return await db.scalar(statement.limit(1)) is not None


def validate_username(value: str) -> str:
    """Validate new/renamed sign-in identifiers without changing legacy accounts."""
    error = username_rule_error(value)
    if error:
        raise ValueError(error)
    return value.strip()


DISPLAY_NAME_MAX_LENGTH: Final = 64
_DISPLAY_NAME_BLOCKED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def validate_display_name(value: str) -> str | None:
    """Normalise and validate an optional visible name; blank means "use the username".

    Unicode letters, marks, emoji and spaces are allowed. Control, format (including
    bidirectional overrides and zero-width characters), surrogate, private-use,
    unassigned and line/paragraph separator characters are rejected so a name cannot
    spoof other text. It is display-only and never used for authentication or lookup.
    """
    name = unicodedata.normalize("NFC", value)
    for char in name:
        if unicodedata.category(char) in _DISPLAY_NAME_BLOCKED_CATEGORIES or (
            char.isspace() and char != " "
        ):
            raise ValueError("Display names cannot contain control or invisible characters.")
    name = re.sub(r" {2,}", " ", name).strip()
    if not name:
        return None
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(f"Display names can be at most {DISPLAY_NAME_MAX_LENGTH} characters.")
    return name


def effective_display_name(user: User) -> str:
    """Name shown to people: the chosen display name, else the sign-in username."""
    return user.display_name or user.username


def _argon2id(password: str, salt: bytes, memory_kib: int, time_cost: int, lanes: int) -> bytes:
    return Argon2id(
        salt=salt,
        length=_HASH_BYTES,
        iterations=time_cost,
        lanes=lanes,
        memory_cost=memory_kib,
    ).derive(password.encode("utf-8"))


def hash_password(password: str) -> str:
    """Hash with the configured Argon2id policy; the parameters travel with the hash."""
    salt = secrets.token_bytes(_SALT_BYTES)
    memory, time_cost, lanes = (
        settings.ARGON2_MEMORY_KIB,
        settings.ARGON2_TIME_COST,
        settings.ARGON2_PARALLELISM,
    )
    digest = _argon2id(password, salt, memory, time_cost, lanes)
    return f"argon2id${memory}${time_cost}${lanes}${salt.hex()}${digest.hex()}"


def _argon2_parameters(parts: list[str]) -> tuple[int, int, int, bytes, bytes]:
    memory, time_cost, lanes = (int(value) for value in parts[1:4])
    # Stored parameters are attacker-influenced if the database is; bound the work they request.
    if not (
        8 <= memory <= _ARGON2_MAX_MEMORY_KIB
        and 1 <= time_cost <= _ARGON2_MAX_TIME_COST
        and 1 <= lanes <= _ARGON2_MAX_LANES
    ):
        raise ValueError("unsupported Argon2id parameters")
    salt, digest = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    if len(salt) < 8 or len(digest) != _HASH_BYTES:
        raise ValueError("malformed Argon2id hash")
    return memory, time_cost, lanes, salt, digest


def _scrypt_digest(password: str, parts: list[str]) -> tuple[bytes, bytes]:
    n, r, p = (int(value) for value in parts[1:4])
    salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    if (
        not (2 <= n <= _SCRYPT_MAX_N and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16)
        or not 16 <= len(expected) <= 64
    ):
        raise ValueError("unsupported scrypt parameters")
    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=_SCRYPT_MAX_MEMORY,
    )
    return candidate, expected


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify an `argon2id$...` or legacy `scrypt$...` record; anything else is rejected."""
    try:
        parts = stored_hash.split("$")
        if len(parts) != 6:
            return False
        if parts[0] == "argon2id":
            memory, time_cost, lanes, salt, expected = _argon2_parameters(parts)
            candidate = _argon2id(password, salt, memory, time_cost, lanes)
            return secrets.compare_digest(candidate, expected)
        if parts[0] == "scrypt":
            candidate, expected = _scrypt_digest(password, parts)
            return secrets.compare_digest(candidate, expected)
    except (ValueError, TypeError, OverflowError):
        pass
    return False


def password_needs_rehash(stored_hash: str) -> bool:
    """True for legacy hashes and Argon2id hashes weaker than the configured policy."""
    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "argon2id":
        return True
    try:
        memory, time_cost, lanes = (int(value) for value in parts[1:4])
    except ValueError:
        return True
    return (memory, time_cost, lanes) != (
        settings.ARGON2_MEMORY_KIB,
        settings.ARGON2_TIME_COST,
        settings.ARGON2_PARALLELISM,
    )


def is_legacy_hash(stored_hash: str) -> bool:
    return stored_hash.startswith("scrypt$")


def legacy_timing_hash(password: str) -> str:
    """A scrypt record used only to spend the legacy algorithm's time during sign-in."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=_HASH_BYTES
    )
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_key() -> tuple[str, str, str]:
    secret = secrets.token_urlsafe(32)
    api_key = f"{API_KEY_PREFIX}{secret}"
    return api_key, api_key[:12], hash_token(api_key)


async def revoke_session(db: AsyncSession, session_token: str) -> bool:
    """Revoke one browser session while preserving its audit metadata."""
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.token_hash == hash_token(session_token), UserSession.revoked_at.is_(None)
        )
        .values(revoked_at=int(time.time()))
    )
    await db.commit()
    if result.rowcount:
        AUTH_SESSION_TRANSITIONS.labels("revoked").inc(result.rowcount)
    return bool(result.rowcount)


async def revoke_other_sessions(db: AsyncSession, user_id: UUID, current_token: str | None) -> None:
    """Revoke browser sessions atomically with an account change; retain audit rows."""
    now = int(time.time())
    statement = update(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    )
    if current_token:
        statement = statement.where(UserSession.token_hash != hash_token(current_token))
    await db.execute(statement.values(revoked_at=now))


def session_cookie_name(host: str) -> str:
    """Return a stable cookie name scoped to one application host and port."""
    normalized_host = host.strip().lower()
    host_hash = hashlib.sha256(normalized_host.encode("utf-8")).hexdigest()[:16]
    return f"{SESSION_COOKIE_PREFIX}{host_hash}"


async def purge_expired_sessions(db: AsyncSession, now: int | None = None) -> int:
    """Delete sessions past their expiry (#142/#150). Authentication already
    ignores them, but nothing ever removed them, so the table only grew.
    Never touches a session that is still valid."""
    cutoff = int(time.time()) if now is None else now
    result = await db.execute(delete(UserSession).where(UserSession.expires_at <= cutoff))
    await db.commit()
    if result.rowcount:
        AUTH_SESSION_TRANSITIONS.labels("expired").inc(result.rowcount)
    return int(result.rowcount or 0)


APP_GRANT_ATTRIBUTE: Final = "__app_grant_scope__"


def app_grant(scope: str) -> Callable[[_Endpoint], _Endpoint]:
    """Let a user-approved app credential (`utd_`) call this route with `scope`.

    Everything else about the route is unchanged: sessions and `utk_` keys still
    work, the handler sees the consenting user, and object checks stay the same.
    Without this marker `get_current_user` refuses every `utd_` credential."""

    def mark(endpoint: _Endpoint) -> _Endpoint:
        setattr(endpoint, APP_GRANT_ATTRIBUTE, scope)
        return endpoint

    return mark


def app_grant_scope(request: Request) -> str | None:
    """The scope the matched route accepts from an app credential, if any."""
    return getattr(request.scope.get("endpoint"), APP_GRANT_ATTRIBUTE, None)


async def _app_grant_user(request: Request, db: AsyncSession, token: str, scope: str) -> User:
    # Imported here: the delegation feature imports this module for token hashing.
    from src.features.delegation import (  # pylint: disable=import-outside-toplevel
        authenticate_grant,
    )

    grant, user = await authenticate_grant(db, token)
    if scope not in grant.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This app has not been granted the required capability.",
        )
    request.state.app_grant = grant
    # Behaves like the API key it replaces: automation, never a browser sign-in.
    request.state.authenticated_actor = AuthenticatedActor(user.id, "api_key")
    return user


async def _resolve_user_credentials(
    request: Request,
    db: AsyncSession = _DB_DEPENDENCY,
) -> User:
    user: User | None = None
    session_token = request.cookies.get(session_cookie_name(request.headers.get("host", "")))
    authorization = request.headers.get("authorization")
    now = int(time.time())

    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        api_key = authorization[7:].strip()
        scope = app_grant_scope(request)
        if api_key.startswith("utd_") and scope is not None:
            return await _app_grant_user(request, db, api_key, scope)
        if api_key.startswith(("utd_", "utp_")):
            raise HTTPException(
                status_code=403,
                detail="Integration credentials can only access their scoped integration APIs.",
            )
        if api_key.startswith("utpm_"):
            raise HTTPException(
                status_code=403, detail="Plugin management tokens cannot access application APIs."
            )
        if not api_key.startswith(API_KEY_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = await db.scalar(
            select(User)
            .join(UserApiKey, UserApiKey.user_id == User.id)
            .where(
                UserApiKey.key_hash == hash_token(api_key),
                UserApiKey.revoked_at.is_(None),
                User.is_active.is_(True),
            )
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    elif session_token:
        user = await db.scalar(
            select(User)
            .join(UserSession, UserSession.user_id == User.id)
            .where(
                UserSession.token_hash == hash_token(session_token),
                UserSession.expires_at > now,
                UserSession.revoked_at.is_(None),
                User.is_active.is_(True),
            )
        )

        if user is not None:
            await db.execute(
                update(UserSession)
                .where(
                    UserSession.token_hash == hash_token(session_token),
                    UserSession.revoked_at.is_(None),
                    UserSession.last_seen_at <= now - 60,
                )
                .values(last_seen_at=now)
            )
            await db.commit()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.authenticated_actor = AuthenticatedActor(
        user.id, "api_key" if authorization else "session"
    )
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = _DB_DEPENDENCY,
) -> User:
    authorization = request.headers.get("authorization", "")
    session_token = request.cookies.get(session_cookie_name(request.headers.get("host", "")))
    needs_lookup = (
        (
            authorization.startswith("Bearer " + API_KEY_PREFIX)
            or (authorization.startswith("Bearer utd_") and app_grant_scope(request) is not None)
        )
        if authorization
        else bool(session_token)
    )
    if not needs_lookup:
        # Unsupported namespaces/missing credentials already fail without database work.
        return await _resolve_user_credentials(request, db)
    return await check_credential(request, lambda: _resolve_user_credentials(request, db))


async def ensure_primary_user(db: AsyncSession) -> User:
    """Create the configured admin account once and return it."""
    username = settings.PRIMARY_USER_USERNAME.strip()
    email = settings.PRIMARY_USER_EMAIL.strip().lower()
    if not username or not email or not settings.PRIMARY_USER_PASSWORD:
        raise RuntimeError("Primary user username, email, and password must be configured.")

    user = await db.scalar(select(User).where(User.username == username))
    if user is None:
        user = await db.scalar(select(User).where(User.email == email))

    if user is None:
        try:
            username = validate_username(username)
        except ValueError as exc:
            raise RuntimeError(f"Invalid primary user username: {exc}") from exc
        # only a new account takes its password from the environment, so
        # only then does the policy apply; checking it on every start made a
        # later policy change crash startup for an account that already exists
        try:
            validate_password(settings.PRIMARY_USER_PASSWORD)
        except ValueError as exc:
            raise RuntimeError(f"Invalid primary user password: {exc}") from exc
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(settings.PRIMARY_USER_PASSWORD),
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    if user.email != email:
        raise RuntimeError("Primary user username and email point to different accounts.")
    if not user.is_admin:
        user.is_admin = True
        await db.commit()
        await db.refresh(user)
    return user


_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


async def get_current_actor(
    request: Request, user: User = _CURRENT_USER_DEPENDENCY
) -> AuthenticatedActor:
    """Retain credential provenance while keeping existing user dependencies."""
    actor = getattr(request.state, "authenticated_actor", None)
    if isinstance(actor, AuthenticatedActor) and actor.user_id == user.id:
        return actor
    # An internal caller/overridden user dependency is not a browser sign-in.
    return AuthenticatedActor(user.id, "api_key")


async def get_current_admin(user: User = _CURRENT_USER_DEPENDENCY) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return user
