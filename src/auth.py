"""Dashboard authentication.

The dashboard exposes live portfolio data and bot controls, so it is
password-protected whenever DASHBOARD_PASSWORD is set. If no password is
configured the dashboard stays open (useful on localhost) but the bot
logs a loud warning at startup.
"""
import hmac
import logging
import time
from functools import wraps

from flask import jsonify, redirect, request, session

logger = logging.getLogger(__name__)

# ip -> (failed_attempts, locked_until_timestamp)
_failed_attempts: dict[str, tuple[int, float]] = {}

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def auth_enabled(config) -> bool:
    return bool(config.DASHBOARD_PASSWORD)


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def is_locked_out() -> tuple[bool, int]:
    """Return (locked, seconds_remaining) for the calling IP."""
    ip = _client_ip()
    attempts, locked_until = _failed_attempts.get(ip, (0, 0.0))
    if locked_until > time.time():
        return True, int(locked_until - time.time())
    if locked_until and locked_until <= time.time():
        _failed_attempts.pop(ip, None)
    return False, 0


def record_failure():
    ip = _client_ip()
    attempts, _ = _failed_attempts.get(ip, (0, 0.0))
    attempts += 1
    locked_until = time.time() + LOCKOUT_SECONDS if attempts >= MAX_ATTEMPTS else 0.0
    _failed_attempts[ip] = (attempts, locked_until)
    if locked_until:
        logger.warning("Login locked out for %s after %d failed attempts",
                       ip, attempts)


def record_success():
    _failed_attempts.pop(_client_ip(), None)


def check_credentials(config, username: str, password: str) -> bool:
    """Constant-time credential check."""
    if not auth_enabled(config):
        return True
    user_ok = hmac.compare_digest(
        (username or "").encode(), config.DASHBOARD_USER.encode())
    pass_ok = hmac.compare_digest(
        (password or "").encode(), config.DASHBOARD_PASSWORD.encode())
    return user_ok and pass_ok


def login_required(config_getter):
    """Decorator factory — guards a route unless auth is disabled."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            config = config_getter()
            if not auth_enabled(config):
                return view(*args, **kwargs)
            if session.get("authenticated"):
                return view(*args, **kwargs)
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return wrapper
    return decorator
