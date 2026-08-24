"""Password and opaque bearer-token primitives using the Python standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


PASSWORD_SCHEME = "pbkdf2_sha256"


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("A valid email address is required.")
    return normalized


def hash_password(password: str, *, iterations: int) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")
    salt = secrets.token_bytes(18)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, raw_hash = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_hash.encode("ascii"))
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def create_bearer_token() -> str:
    return secrets.token_urlsafe(48)


def hash_bearer_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginRateLimiter:
    """Small in-process limiter for login attempts.

    Cloud deployments should additionally rate-limit at the reverse proxy. This
    limiter remains useful for local, single-process and defense-in-depth use.
    """

    def __init__(self, *, attempts: int, window_seconds: int) -> None:
        from threading import Lock

        self.attempts = attempts
        self.window_seconds = window_seconds
        self._entries: dict[str, list[float]] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        threshold = now - self.window_seconds
        return [value for value in self._entries.get(key, []) if value >= threshold]

    def assert_allowed(self, key: str) -> None:
        from time import monotonic

        now = monotonic()
        with self._lock:
            recent = self._recent(key, now)
            self._entries[key] = recent
            if len(recent) >= self.attempts:
                raise PermissionError("Too many login attempts. Wait before trying again.")

    def record_failure(self, key: str) -> None:
        from time import monotonic

        now = monotonic()
        with self._lock:
            recent = self._recent(key, now)
            recent.append(now)
            self._entries[key] = recent

    def record_success(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
