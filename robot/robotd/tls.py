"""TLS and secret hygiene for robotd's connections to the backend (sprint 6 P0.7, server mode).

- `client_ssl_context(cfg)`: the context for every https/wss request robotd makes to the backend
  (the /ws/robot WebSocket, the page asset sync and the read-only proxy). Certificate verification
  and the hostname check are ALWAYS on; there is no setting to turn them off. By default the
  system CA store is used (Let's Encrypt is trusted by Raspberry Pi OS). `BACKEND_CA_FILE` replaces
  the system store with one CA bundle - only for a test CA or a self-hosted CA (e.g. Caddy's
  `internal` CA in a rehearsal), never needed with a public certificate.
- `redact(text)`: removes the robot token from any text that may hold a backend URL (aiohttp's
  handshake errors print the full URL, query included). `install_log_redaction()` adds the same
  as a logging filter on the root handlers, so journald never sees `token=<value>`.
"""

import logging
import re
import ssl

from robotd.config import Config

_TOKEN_RE = re.compile(r"(token=)[^&\s'\"<>)\]]*", re.IGNORECASE)
_secrets: set[str] = set()


def client_ssl_context(cfg: Config) -> ssl.SSLContext:
    """A verifying client context: CERT_REQUIRED + hostname check, system CAs or BACKEND_CA_FILE."""
    if cfg.backend_ca_file:
        ctx = ssl.create_default_context(cafile=cfg.backend_ca_file)  # only this bundle
    else:
        ctx = ssl.create_default_context()  # the system store
    # create_default_context already sets both; asserted so no later change can weaken them
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    return ctx


def register_secret(value: str) -> None:
    """A literal value (the robot token) that must never appear in a log line."""
    if value and len(value) >= 4:
        _secrets.add(value)


def redact(text: str) -> str:
    text = _TOKEN_RE.sub(r"\1***", text)
    for value in _secrets:
        text = text.replace(value, "***")
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string: leave it to the handler
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


def install_log_redaction() -> None:
    """Attach the filter to every root handler (robotd, aiohttp and anything else that logs)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())
