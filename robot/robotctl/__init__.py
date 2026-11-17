"""robotctl: the bring-up and calibration tool of the Smart Campus Robot (docs/BRINGUP.md).

It talks to the ESP32 through robotd's local control API (`/ws/ctl`) when robotd runs, or
straight to the serial port when robotd is stopped. Every step prints what to do, refuses to move
anything without a typed confirmation, stops on its own (STOP + MODE SAFE on every exit) and
writes its result, with the raw measurements, to the calibration file (through
`robotd.calibration`) and to a local calibration log.

Anything measured against the SIMULATED ESP32 is labelled SIMULATED everywhere and is never a
physical result (CLAUDE.md rule 5).
"""

__version__ = "0.1.0"
