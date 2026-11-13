"""Contract check for outgoing robot messages (sprint 3 S1).

In the test environment every message the backend sends is validated against the `scr_protocol`
models, so the whole test suite doubles as a contract test: an undocumented field or message
fails the test that produced it. Outside tests the wrapper does nothing.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from scr_protocol import ProtocolError, parse_backend_message

from app.core.config import get_settings

Send = Callable[[dict[str, Any]], Awaitable[None]]


class ContractViolation(AssertionError):
    pass


def checked(send: Send) -> Send:
    if get_settings().app_env != "test" or getattr(send, "_contract_checked", False):
        return send

    async def strict(msg: dict[str, Any]) -> None:
        try:
            parse_backend_message(msg)
        except ProtocolError as e:
            raise ContractViolation(
                f"backend message not in the protocol contract: {msg.get('type')!r}: {e}"
            ) from e
        await send(msg)

    strict._contract_checked = True  # type: ignore[attr-defined]
    return strict
