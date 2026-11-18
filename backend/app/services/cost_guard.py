"""Cost guard (sprint 6, server mode): who may spend OpenAI credit, and how fast.

Two independent limits:
- **Per-IP rate limits** (sliding window of 60 s, in this process): every HTTP request or
  WebSocket turn that may call a paid provider (questions, TTS, STT, conversation turns) takes
  one slot. Connections with the robot token get RATE_LIMIT_ROBOT_PER_MIN, visitors
  RATE_LIMIT_PAID_PER_MIN.
- **A global daily cap** (DAILY_PAID_CALL_LIMIT): every real provider call (embedding, STT, LLM,
  TTS; cache hits are not calls) is counted in the database per day in LOCAL_TIMEZONE, and is
  refused once the day's count reached the limit. The check happens in services/resilience.py,
  which every provider call goes through.

Over a limit the provider call raises `PaidLimitError`, a `ProviderError`: the conversation gives
its usual polite "weak internet" reply and HTTP endpoints answer 429. Nothing crashes, and the
circuit breaker is not touched (the provider itself is fine).

Both limits are on by default only with APP_ENV=production; in development and tests they are
off unless the settings are given (tests set them). With FAKE_PROVIDERS=true the fake calls are
counted and limited too, on purpose: a rehearsal can trigger the guard without spending credit.

The rate limiter is per process (WEB_CONCURRENCY=1 in production, see docs/DEPLOY_SERVER.md).
"""

import logging
import time
from collections import deque
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.config import get_settings
from app.services.providers import ProviderError

log = logging.getLogger(__name__)

Caller = Literal["robot", "visitor"]
WINDOW_S = 60.0


class PaidLimitError(ProviderError):
    """A rate limit or the daily cap refused a paid call (handled like a provider outage)."""


# ---------------- per-IP sliding windows ----------------
class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, window_s: float = WINDOW_S) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        if len(self._hits) > 10_000:  # forget idle addresses (bounded memory)
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= window_s]:
                self._hits.pop(k, None)
        return True

    def clear(self) -> None:
        self._hits.clear()


_limiter = SlidingWindowLimiter()
_refused: dict[str, float | int | None] = {"rate_limited": 0, "last_rate_limited_at": None}


def rate_limit(caller: Caller) -> int | None:
    """The per-minute limit for this kind of caller, or None (off)."""
    s = get_settings()
    return s.rate_limit_robot_per_min if caller == "robot" else s.rate_limit_paid_per_min


def allow_paid_request(caller: Caller, ip: str) -> bool:
    """Take one slot for a request or turn that may call a paid provider."""
    limit = rate_limit(caller)
    if limit is None:
        return True
    if _limiter.allow(f"{caller}:{ip}", limit):
        return True
    _refused["rate_limited"] = int(_refused["rate_limited"] or 0) + 1
    _refused["last_rate_limited_at"] = time.time()
    log.warning("rate limit: %s %s over %d per minute", caller, ip, limit)
    return False


def reset_limits() -> None:
    _limiter.clear()
    _refused.update(rate_limited=0, last_rate_limited_at=None)


def rate_limit_stats() -> dict[str, float | int | None]:
    return dict(_refused)


# ---------------- global daily cap (database) ----------------
def local_day() -> date:
    return datetime.now(ZoneInfo(get_settings().local_timezone)).date()


_TAKE = text(
    """
    INSERT INTO paid_call_days (day, calls, refused) VALUES (:day, 1, 0)
    ON CONFLICT (day) DO UPDATE SET calls = paid_call_days.calls + 1, updated_at = now()
        WHERE paid_call_days.calls < :limit
    RETURNING calls
    """
)
_REFUSE = text(
    """
    INSERT INTO paid_call_days (day, calls, refused) VALUES (:day, 0, 1)
    ON CONFLICT (day) DO UPDATE SET refused = paid_call_days.refused + 1, updated_at = now()
    """
)


async def take_paid_call(provider: str) -> None:
    """Count one real provider call for today, or raise PaidLimitError when the daily cap is
    reached. Called right before each attempt (retries are real calls too). Off when
    DAILY_PAID_CALL_LIMIT is not set (development default)."""
    limit = get_settings().daily_paid_call_limit
    if limit is None:
        return
    from app.db.session import get_sessionmaker

    day = local_day()
    try:
        async with get_sessionmaker()() as db:
            taken = await db.execute(_TAKE, {"day": day, "limit": limit}) if limit > 0 else None
            row = taken.first() if taken is not None else None
            if row is None:
                await db.execute(_REFUSE, {"day": day})
            await db.commit()
    except Exception as e:  # noqa: BLE001 - no counter, no paid call (fail closed)
        log.exception("paid-call counter unavailable")
        raise PaidLimitError(f"paid-call counter unavailable: {type(e).__name__}") from e
    if row is None:
        log.warning("daily paid-call limit %d reached: %s call refused", limit, provider)
        raise PaidLimitError(f"daily limit of paid AI calls reached ({limit}); try tomorrow")


async def usage_today() -> dict[str, object]:
    """{"date", "paid_calls", "refused", "limit"} for today (LOCAL_TIMEZONE)."""
    from app.db.session import get_sessionmaker

    day = local_day()
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                text("SELECT calls, refused FROM paid_call_days WHERE day = :day"), {"day": day}
            )
        ).first()
    return {
        "date": day.isoformat(),
        "paid_calls": int(row[0]) if row else 0,
        "refused": int(row[1]) if row else 0,
        "limit": get_settings().daily_paid_call_limit,
    }
