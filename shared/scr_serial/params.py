"""Runtime parameters of the ESP32 firmware (docs/SERIAL_PROTOCOL.md section 10).

`params.json` next to this file is the single source of the table: the Python side reads it
here, and the firmware compiles a header generated from it (`scr_serial.gen_params_header`).
"""

import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

PARAMS_FILE = Path(__file__).resolve().parent / "params.json"
FNV_OFFSET = 0x811C9DC5
FNV_PRIME = 0x01000193


@dataclass(frozen=True)
class ParamSpec:
    id: int
    name: str
    unit: str
    type: Literal["float", "int", "bool"]
    default: float
    min: float
    max: float
    group: str
    required: bool
    step: str
    note: str

    def in_range(self, value: float) -> bool:
        return self.min <= f32(value) <= self.max if self.type == "float" else (
            self.min <= value <= self.max and float(value).is_integer()
        )  # fmt: skip


@dataclass(frozen=True)
class ParamTable:
    version: int
    params: tuple[ParamSpec, ...]

    def by_id(self, pid: int) -> ParamSpec:
        for p in self.params:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def by_name(self, name: str) -> ParamSpec:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(name)

    def defaults(self) -> dict[int, float]:
        return {p.id: float(p.default) for p in self.params}

    def required_names(self) -> list[str]:
        return [p.name for p in self.params if p.required]


@lru_cache(maxsize=4)
def load_table(path: Path = PARAMS_FILE) -> ParamTable:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    params = tuple(
        ParamSpec(
            id=int(p["id"]), name=p["name"], unit=p["unit"], type=p["type"],
            default=float(p["default"]), min=float(p["min"]), max=float(p["max"]),
            group=p["group"], required=bool(p["required"]), step=p["step"], note=p["note"],
        )
        for p in data["params"]
    )  # fmt: skip
    ids = [p.id for p in params]
    if ids != sorted(ids) or len(set(ids)) != len(ids) or min(ids) < 1 or max(ids) >= 0xFFFF:
        raise ValueError("parameter ids must be unique, ascending and in 1..0xFFFE")
    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise ValueError("parameter names must be unique")
    for p in params:
        if not (p.min <= p.default <= p.max):
            raise ValueError(f"{p.name}: default outside [min, max]")
    return ParamTable(version=int(data["version"]), params=params)


def f32(value: float) -> float:
    """The value as the firmware stores it (IEEE-754 single precision)."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def param_hash(values: dict[int, float]) -> int:
    """FNV-1a 32 over (id u16 LE, value f32 LE) of every parameter in ascending id order.

    `values` must hold every parameter of the table (use `{**table.defaults(), **changes}`)."""
    h = FNV_OFFSET
    for pid in sorted(values):
        for byte in struct.pack("<Hf", pid, values[pid]):
            h ^= byte
            h = (h * FNV_PRIME) & 0xFFFFFFFF
    return h
