"""
NexLink Server — Security: JWT + API Key Management
====================================================
Two-layer authentication:

  Layer 1 — API Key:
    Agent generates and stores a raw key locally.
    Server stores sha256(key) in DB. Never the raw key.
    Used for: device registration, token refresh.

  Layer 2 — JWT Access Token:
    Issued after API key verification.
    Short-lived (default 60 min). Signed with HS256.
    Used for: all subsequent REST + WebSocket connections.

JWT Payload:
    {
      "sub":  "<device_id (UUID string)>",
      "aid":  "<agent_id>",
      "type": "access" | "refresh",
      "iat":  <issued_at UNIX timestamp>,
      "exp":  <expiry UNIX timestamp>
    }

API Key Format:
    Raw:    64 hex chars (secrets.token_hex(32)) — given to agent ONCE
    Stored: sha256(raw).hexdigest() — never raw key in DB
    Prefix: first 8 chars — stored for human-readable lookup hint
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from server.config.settings import ServerSettings, get_settings
from server.core.exceptions import TokenExpiredError, TokenInvalidError

# ── Password/Key Hashing Context ──────────────────────────────────────────────
# bcrypt is used for API-key lookup in verify_api_key().
# We use sha256 for API keys (fast lookup) and bcrypt for user passwords if added later.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── API Key Utilities ─────────────────────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        (raw_key, key_hash, key_prefix)
        - raw_key:    64 hex chars. Give this to the agent ONCE and discard.
        - key_hash:   sha256(raw_key). Store this in the DB.
        - key_prefix: first 8 chars. Store for lookup hints and audit.

    Why sha256 not bcrypt for API keys?
    bcrypt is intentionally slow (to resist offline brute-force on password hashes).
    API keys are 32 bytes of entropy — sha256 is sufficient because there's no
    brute-force risk on 256-bit entropy keys. bcrypt would add 200ms per request.
    """
    raw_key = secrets.token_hex(32)          # 64 chars, 256 bits of entropy
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix


def hash_api_key(raw_key: str) -> str:
    """Hash a raw API key for storage. sha256 is sufficient for 256-bit entropy keys."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """Verify a raw API key against its stored sha256 hash."""
    return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash


# ── JWT Utilities ─────────────────────────────────────────────────────────────

def create_access_token(
    device_id: str,
    agent_id: str,
    settings: ServerSettings | None = None,
) -> str:
    """
    Create a short-lived JWT access token.

    The token is signed with HS256 (HMAC-SHA256) using the server's secret key.
    The secret key must be at least 256 bits (32 bytes) for HS256 to be secure.

    Args:
        device_id: The hardware DEVICE_ID (UUID v5 string) from the agent.
        agent_id:  The human-readable agent identifier.

    Returns:
        Signed JWT string. Include in Authorization: Bearer <token> header.
    """
    if settings is None:
        settings = get_settings()

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(device_id),                               # subject = device_id
        "aid": agent_id,                                     # agent_id claim
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    device_id: str,
    agent_id: str,
    settings: ServerSettings | None = None,
) -> str:
    """
    Create a long-lived JWT refresh token.

    Refresh tokens are used to get new access tokens without re-authenticating
    with the API key. They should be stored securely on the agent.
    """
    if settings is None:
        settings = get_settings()

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(device_id),
        "aid": agent_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(
    token: str,
    settings: ServerSettings | None = None,
    expected_type: str = "access",
) -> dict:
    """
    Decode and verify a JWT token.

    Verifies:
    - Signature (using server secret key)
    - Expiry (exp claim)
    - Token type (access vs refresh)

    Raises:
        TokenExpiredError:  Token has expired.
        TokenInvalidError:  Signature invalid, malformed, or wrong type.

    Returns:
        Decoded payload dict: {"sub": device_id, "aid": agent_id, "type": ..., ...}
    """
    if settings is None:
        settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token has expired. Re-authenticate with your API key.")
    except JWTError as e:
        raise TokenInvalidError(f"Invalid token: {e}")

    if payload.get("type") != expected_type:
        raise TokenInvalidError(
            f"Expected '{expected_type}' token, got '{payload.get('type')}'"
        )

    return payload


def extract_device_id(token: str, settings: ServerSettings | None = None) -> str:
    """
    Convenience: decode token and return the device_id (sub claim).
    Used in WebSocket auth where we just need the device_id quickly.
    """
    payload = decode_token(token, settings=settings, expected_type="access")
    return payload["sub"]
