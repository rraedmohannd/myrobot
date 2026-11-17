"""Serial messages <-> the JSON of robotd's control API (`ctl.serial` / `ctl.serial_rx`).

A message is `{"frame_type": "<MsgType name>", "fields": {<dataclass field>: value}}`; enums travel
as their integer value (names are accepted when reading), tuples as lists.
"""

import dataclasses
import enum
import typing
from functools import cache
from typing import Any

from scr_serial import Message, MsgType
from scr_serial.messages import MESSAGE_CLASSES


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def to_json(msg: Message) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(msg):  # type: ignore[arg-type]
        value = getattr(msg, f.name)
        if isinstance(value, enum.Enum):
            value = int(value.value)
        elif isinstance(value, tuple):
            value = list(value)
        fields[f.name] = value
    return {"frame_type": MsgType(msg.TYPE).name, "fields": fields}


def _convert(hint: Any, value: Any) -> Any:
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        if isinstance(value, str):
            return hint[value] if value in hint.__members__ else hint(int(value))
        return hint(value)
    if typing.get_origin(hint) is tuple:
        return tuple(value)
    return value


def from_json(frame_type: str | int, fields: dict[str, Any]) -> Message:
    """Build a message from `ctl.serial_rx` data. Raises ValueError for anything unknown."""
    try:
        mtype = MsgType[frame_type] if isinstance(frame_type, str) else MsgType(frame_type)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown frame type {frame_type!r}") from exc
    cls = MESSAGE_CLASSES[int(mtype)]
    hints = _hints(cls)
    kwargs = {
        f.name: _convert(hints.get(f.name), fields[f.name])
        for f in dataclasses.fields(cls)
        if f.name in fields
    }
    try:
        return cls(**kwargs)  # type: ignore[no-any-return]
    except TypeError as exc:
        raise ValueError(f"bad fields for {mtype.name}: {exc}") from exc
