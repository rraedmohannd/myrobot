"""Who may use the paid endpoints (sprint 6, server mode, APP_ENV=production).

`/robot`, `/voice`, `/api/chat`, `/api/tts*` and `/ws/robot` need either
- the **robot token** (ROBOT_TOKEN): `?token=`, `Authorization: Bearer <token>` or
  `X-Robot-Token: <token>`; or
- a **visitor session**: `/access` asks once for SITE_ACCESS_CODE and sets a signed cookie
  (HMAC-SHA256 with SECRET_KEY, valid VISITOR_SESSION_HOURS). Browsers send it with every
  same-origin request, including the WebSocket upgrade of the robot page.

With PUBLIC_PAGES=false only the robot token works. In development and tests nothing here
changes the old behaviour: the pages and the API stay open, /ws/robot keeps its LAN rules.

The client address is always `request.client.host`. Behind the reverse proxy uvicorn sets it
from X-Forwarded-For only for connections from FORWARDED_ALLOW_IPS (`--proxy-headers`); this
module never reads X-Forwarded-For itself, so a visitor cannot choose their own address.
"""

import hashlib
import hmac
import time
from secrets import compare_digest

from starlette.requests import HTTPConnection

from app.core.config import get_settings
from app.services.cost_guard import Caller

VISITOR_COOKIE = "visitor_session"


def client_ip(conn: HTTPConnection) -> str:
    return conn.client.host if conn.client else "unknown"


def robot_token_from(conn: HTTPConnection) -> str | None:
    """The token as sent by the robot: query `token`, `Authorization: Bearer`, `X-Robot-Token`."""
    token = conn.query_params.get("token")
    if token:
        return token
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return conn.headers.get("x-robot-token") or None


def robot_token_valid(conn: HTTPConnection) -> bool:
    expected = get_settings().robot_token
    secret = expected.get_secret_value() if expected is not None else ""
    token = robot_token_from(conn)
    return bool(secret and token and compare_digest(token.encode(), secret.encode()))


# ---------------- visitor cookie ----------------
def _sign(payload: str) -> str:
    key = get_settings().secret_key
    secret = key.get_secret_value() if key is not None else ""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_visitor_cookie(now: float | None = None) -> str:
    """`v1.<expiry unix time>.<hmac>`: nothing about the visitor, just a signed expiry."""
    expires = int((now or time.time()) + get_settings().visitor_session_hours * 3600)
    payload = f"v1.{expires}"
    return f"{payload}.{_sign(payload)}"


def visitor_cookie_valid(value: str | None) -> bool:
    settings = get_settings()
    if not value or settings.secret_key is None or not settings.secret_key.get_secret_value():
        return False
    try:
        version, expires, signature = value.split(".")
        payload = f"{version}.{int(expires)}"
    except ValueError:
        return False
    if version != "v1" or not compare_digest(signature, _sign(payload)):
        return False
    return int(expires) > time.time()


def access_code_ok(code: str) -> bool:
    expected = get_settings().site_access_code
    secret = expected.get_secret_value() if expected is not None else ""
    return bool(secret) and compare_digest(code.strip().encode(), secret.encode())


# ---------------- who is calling ----------------
def paid_caller(conn: HTTPConnection) -> Caller | None:
    """ "robot", "visitor" or None (not allowed). Outside production everyone is allowed: the
    robot token makes a "robot", anyone else counts as a "visitor" for the (optional) limits."""
    settings = get_settings()
    if robot_token_valid(conn):
        return "robot"
    if not settings.is_production:
        return "visitor"
    if settings.public_pages and visitor_cookie_valid(conn.cookies.get(VISITOR_COOKIE)):
        return "visitor"
    return None


def safe_next(target: str | None, default: str = "/robot") -> str:
    """Only a local path may follow the access page (no `//host`, no scheme, no backslash)."""
    if (
        not target
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or any(ord(c) < 32 for c in target)
    ):
        return default
    return target
