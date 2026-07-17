"""
Session token signing/parsing — HMAC-SHA256 over a base64url payload.

Token format: <base64url_payload>.<hex_signature>
Payload: JSON {"slug": "...", "exp": <unix_ts>}
Secret resolution:
    1. LUNA_SESSION_SECRET env var (preferred for Railway)
    2. data/.session_secret file (auto-generated for local dev, 32 random bytes, mode 0o600)

Stdlib only — no itsdangerous / jose dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from luna.core.paths import data_dir

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "luna_session"
DEFAULT_TTL_SECONDS = 30 * 86400  # 30 days

_secret_warning_emitted = False


def _session_secret_file() -> Path:
    return data_dir() / ".session_secret"


def _get_secret() -> bytes:
    """Resolve the HMAC secret. Env var wins; file fallback for local dev."""
    global _secret_warning_emitted
    env = os.environ.get("LUNA_SESSION_SECRET")
    if env:
        if len(env) < 32:
            raise RuntimeError(
                "LUNA_SESSION_SECRET is too short — use at least 32 bytes "
                "(generate via `python -c 'import secrets; print(secrets.token_hex(32))'`)."
            )
        return env.encode("utf-8")

    path = _session_secret_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if not _secret_warning_emitted:
            logger.warning(
                "Auto-generated session secret at %s. For Railway/production set "
                "LUNA_SESSION_SECRET env var instead — file-based secrets don't survive "
                "ephemeral filesystems.",
                path,
            )
            _secret_warning_emitted = True
    return path.read_bytes()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def make_session_token(slug: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Sign a session token for the given slug. Returns 'payload.signature' string."""
    if not slug or not isinstance(slug, str):
        raise ValueError("slug must be a non-empty string")
    expires_at = int(time.time()) + ttl_seconds
    payload_bytes = json.dumps(
        {"slug": slug, "exp": expires_at}, separators=(",", ":")
    ).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig = hmac.new(_get_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def parse_session_token(token: Optional[str]) -> Optional[str]:
    """Verify token signature + expiry. Returns slug on success, None on any failure.

    Failures (returns None): missing/empty token, malformed format, bad signature,
    expired, malformed JSON payload, missing slug field.
    """
    if not token:
        return None
    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    try:
        expected_sig = hmac.new(
            _get_secret(), payload_b64.encode("ascii"), hashlib.sha256
        ).hexdigest()
    except Exception:
        return None
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload_bytes = _b64url_decode(payload_b64)
        data = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    slug = data.get("slug")
    exp = data.get("exp")
    if not slug or not isinstance(slug, str) or not isinstance(exp, (int, float)):
        return None
    if time.time() > exp:
        return None
    return slug
