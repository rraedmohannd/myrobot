import logging
import re

# `token=<value>` in a logged URL (uvicorn logs "/ws/robot?token=..." and "/robot?token=...").
_TOKEN_QUERY = re.compile(r"((?:^|[?&;\s\"'])token=)[^&\s\"']+", re.IGNORECASE)
REDACTED = "***"
REDACTED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "app")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    install_secret_redaction()


def _secret_values() -> list[str]:
    """The literal secrets of this process (long enough to be unambiguous)."""
    from app.core.config import get_settings

    s = get_settings()
    values = []
    for secret in (
        s.robot_token, s.admin_token, s.site_access_code, s.secret_key, s.openai_api_key,
        s.elevenlabs_api_key, s.postgres_password,
    ):  # fmt: skip
        value = secret.get_secret_value() if secret is not None else ""
        if len(value) >= 6:
            values.append(value)
    return sorted(values, key=len, reverse=True)


def redact(text: str, query: bool = True) -> str:
    if query:
        text = _TOKEN_QUERY.sub(lambda m: m.group(1) + REDACTED, text)
    for value in _secret_values():
        if value in text:
            text = text.replace(value, REDACTED)
    return text


class SecretRedactionFilter(logging.Filter):
    """Removes `token=` query values and the configured secrets (ROBOT_TOKEN, ADMIN_TOKEN,
    SITE_ACCESS_CODE, SECRET_KEY, API keys) from log records, so they never reach `docker logs`.
    The arguments are redacted one by one (uvicorn's access formatter needs them as a tuple)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):  # "?token=%s" in a format string stays a format
                record.msg = redact(record.msg, query=not record.args)
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
        except Exception:  # noqa: BLE001, S110 - logging must never fail because of redaction
            pass
        return True


_FILTER = SecretRedactionFilter()


def install_secret_redaction() -> None:
    """On the loggers that print URLs (uvicorn) and on the root logger and its handlers.
    Idempotent. Logger filters see only records logged on that logger itself, hence the list;
    handler filters also catch records that propagate from other loggers."""
    for name in REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
