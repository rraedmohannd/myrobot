# shared — robot ↔ backend protocol contract (version 2)

One definition of every WebSocket message between the robot (Raspberry Pi, or the browser pages
that stand in for it) and the backend. Human description: `docs/PROTOCOL.md`.

| Path | What it is |
|---|---|
| `scr_protocol/messages.py` | pydantic v2 models, the single source of truth |
| `scr_protocol/__init__.py` | `parse_robot_message`, `parse_backend_message`, `negotiate_version`, constants |
| `scr_protocol/generate.py` | writes the JSON Schema files and the tables of `docs/PROTOCOL.md` |
| `protocol/robot_to_backend.schema.json`, `protocol/backend_to_robot.schema.json` | JSON Schema (draft 2020-12), generated; for clients that are not written in Python |

## Use
- **Backend (Docker):** `docker-compose.yml` mounts this folder at `/shared` and the image has
  `PYTHONPATH=/app:/shared`, so `import scr_protocol` works with live code.
- **Raspberry Pi (sprint 4) or any other machine:** `pip install ./shared` (the only dependency is pydantic).

```python
from scr_protocol import parse_backend_message, messages

msg = parse_backend_message(json.loads(text))        # raises ProtocolError if it is not in the contract
status = messages.RobotStatus(state="IDLE", is_simulated=False, battery_v=7.9)
ws.send(status.model_dump_json(exclude_none=True))
```

## After changing a message
```
docker compose exec backend python -m scr_protocol.generate     # schema files + doc tables
docker compose exec backend pytest -q tests/test_protocol_contract.py
```
The tests fail if the schema files or the tables in `docs/PROTOCOL.md` do not match the models, and
if the backend sends anything that is not in the contract.

## Versions
- **1**: conversation, telemetry, explainers, interest matching (sprints 1 and 2). Still accepted (`/voice`).
- **2**: adds `robot.status` (robot → backend) and `robot.command` / `robot.command.ack`. The backend
  sends `robot.command` only to connections that said `protocol_version: 2` in `hello`.

Safety: every backend → robot command is a request. The robot and the ESP32 decide locally and stop
safely without the backend (CLAUDE.md rule 4).

## Also in this folder (sprint 4)
| Path | What it is |
|---|---|
| `scr_serial/` | Pi ↔ ESP32 serial frames (COBS + CRC-16 + sequence number), standard library only. Human description: `docs/SERIAL_PROTOCOL.md`. The C++ twin is `firmware/lib/protocol` |
| `serial_fixtures/` | the same test frames written by the Python side (`python -m scr_serial.vectors`) and by the C++ native tests; each side's tests decode the other's file |
| `scr_odometry/` | pose update, vector to the start point and room geometry: one implementation used by the backend simulator and by `robotd` |
| `vision_thresholds.json` | engagement / active-speaker thresholds shared by the browser prototype and `visiond` |
