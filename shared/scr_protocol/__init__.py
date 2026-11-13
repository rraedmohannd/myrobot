"""Smart Campus Robot: robot <-> backend message contract (see README.md)."""

from pydantic import TypeAdapter, ValidationError

from scr_protocol import messages
from scr_protocol.messages import (
    BACKEND_TO_ROBOT,
    COMMANDS,
    PROTOCOL_VERSION,
    ROBOT_STATES,
    ROBOT_TO_BACKEND,
    SUPPORTED_VERSIONS,
    BackendMessage,
    BackendToRobot,
    RobotMessage,
    RobotToBackend,
    message_type,
)

__version__ = "2.0.0"

_ROBOT = TypeAdapter(RobotToBackend)
_BACKEND = TypeAdapter(BackendToRobot)
ROBOT_TYPES = frozenset(message_type(m) for m in ROBOT_TO_BACKEND)
BACKEND_TYPES = frozenset(message_type(m) for m in BACKEND_TO_ROBOT)


class ProtocolError(ValueError):
    """A message does not match the contract."""


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(p) for p in err["loc"] if not str(p).startswith(("tagged-union", "function")))
    return f"{where}: {err['msg']}" if where else err["msg"]


def parse_robot_message(data: dict) -> RobotMessage:
    """Validate a robot -> backend message. Raises ProtocolError."""
    try:
        return _ROBOT.validate_python(data)
    except ValidationError as e:
        raise ProtocolError(_first_error(e)) from e


def parse_backend_message(data: dict) -> BackendMessage:
    """Validate a backend -> robot message. Raises ProtocolError."""
    try:
        return _BACKEND.validate_python(data)
    except ValidationError as e:
        raise ProtocolError(_first_error(e)) from e


def negotiate_version(client_version: object) -> int:
    """The version spoken on a connection: the client's, limited to what the backend supports.
    Missing or odd values mean an old client (version 1)."""
    try:
        version = int(client_version)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 1
    return max(min(version, PROTOCOL_VERSION), SUPPORTED_VERSIONS[0])


__all__ = [
    "BACKEND_TO_ROBOT", "BACKEND_TYPES", "COMMANDS", "PROTOCOL_VERSION", "ROBOT_STATES",
    "ROBOT_TO_BACKEND", "ROBOT_TYPES", "SUPPORTED_VERSIONS", "BackendMessage", "ProtocolError",
    "RobotMessage", "messages", "message_type", "negotiate_version", "parse_backend_message",
    "parse_robot_message",
]  # fmt: skip
