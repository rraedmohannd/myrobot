"""Generate the JSON Schema files and the message tables of docs/PROTOCOL.md from the models.

    python -m scr_protocol.generate            # rewrite shared/protocol/*.json and the doc tables
    python -m scr_protocol.generate --check    # exit 1 if a file is out of date (used by tests)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from scr_protocol.messages import (
    BACKEND_TO_ROBOT,
    PROTOCOL_VERSION,
    ROBOT_TO_BACKEND,
    BackendToRobot,
    RobotToBackend,
    message_type,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "protocol"
DOC = ROOT.parent / "docs" / "PROTOCOL.md"
ENVELOPE_FIELDS = ("type", "id", "ts")


def schema(direction: str) -> dict[str, Any]:
    union = RobotToBackend if direction == "robot_to_backend" else BackendToRobot
    body = TypeAdapter(union).json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"smart-campus-robot/protocol/v{PROTOCOL_VERSION}/{direction}.schema.json",
        "title": f"Smart Campus Robot protocol v{PROTOCOL_VERSION}: {direction.replace('_', ' ')}",
        **body,
    }


def schema_files() -> dict[Path, str]:
    return {
        SCHEMA_DIR / f"{d}.schema.json": json.dumps(schema(d), indent=2, ensure_ascii=False) + "\n"
        for d in ("robot_to_backend", "backend_to_robot")
    }


def _fields(model: type[BaseModel]) -> str:
    names = []
    for name, field in model.model_fields.items():
        if name in ENVELOPE_FIELDS and not (name == "id" and field.is_required()):
            continue
        names.append(f"`{name}`" if field.is_required() else f"`{name}?`")
    return ", ".join(names) or "–"


def table(models: tuple[type[BaseModel], ...]) -> str:
    lines = ["| type | fields | meaning |", "|---|---|---|"]
    for model in models:
        meaning = re.sub(r"\s+", " ", (model.__doc__ or "").strip()).replace("|", "\\|")
        lines.append(f"| `{message_type(model)}` | {_fields(model)} | {meaning} |")
    return "\n".join(lines)


TABLES = {"robot-to-backend": ROBOT_TO_BACKEND, "backend-to-robot": BACKEND_TO_ROBOT}


def render_doc(text: str) -> str:
    """Replace the generated blocks of docs/PROTOCOL.md (between BEGIN/END GENERATED marks)."""
    for name, models in TABLES.items():
        begin, end = f"<!-- BEGIN GENERATED {name} -->", f"<!-- END GENERATED {name} -->"
        if begin not in text or end not in text:
            raise SystemExit(f"docs/PROTOCOL.md has no generated block '{name}'")
        head, rest = text.split(begin, 1)
        _, tail = rest.split(end, 1)
        text = f"{head}{begin}\n{table(models)}\n{end}{tail}"
    return text


def main() -> None:
    check = "--check" in sys.argv
    stale = []
    for path, content in schema_files().items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            stale.append(path)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
    if DOC.exists():
        current = DOC.read_text(encoding="utf-8")
        wanted = render_doc(current)
        if wanted != current:
            stale.append(DOC)
            if not check:
                DOC.write_text(wanted, encoding="utf-8", newline="\n")
    for path in stale:
        print(("out of date: " if check else "written: ") + str(path))
    if check and stale:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
