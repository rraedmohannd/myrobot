"""motor-test and encoder-check (BRINGUP.md sections 3 and 4)."""

import argparse

from scr_serial import DriveMode

from robotctl.operator import Refused
from robotctl.session import MAX_TEST_DUTY, Session, StepError, enc_delta
from robotctl.steps import CHECK_FAILED

ENC_KEYS = ("ENCODER_INVERT_FL", "ENCODER_INVERT_RL", "ENCODER_INVERT_FR", "ENCODER_INVERT_RR")
ENC_NAMES = ("front-left", "rear-left", "front-right", "rear-right")
SIDE_ENC = {"left": (0, 1), "right": (2, 3)}
MIN_TICKS = 10  # fewer than this in a test counts as "did not count"
TRACKS_OFF = "tracks off the ground"
CAP_KEYS = ("TEST_MAX_DUTY", "MOTOR_MAX_DUTY", "MAX_SPEED_MM_S", "RAMP_MM_S2")


async def run_motor_test(s: Session, args: argparse.Namespace) -> int:
    side, duty, secs = args.side, float(args.duty), float(args.secs)
    if not 0.0 < duty <= MAX_TEST_DUTY:
        raise Refused(f"--duty {duty}: must be in (0, {MAX_TEST_DUTY}]")
    if not 0.0 < secs <= 5.0:
        raise Refused(f"--secs {secs}: must be in (0, 5]")
    s.say(
        f"motor-test {side}: open loop, duty {duty:g} for {secs:g} s in MODE TEST.\n"
        "  1. Robot on a stand, BOTH tracks clear of the ground and of hands, cables, tools.\n"
        "  2. Hand near the battery switch (the emergency stop).\n"
        f"  3. Watch which way the {side} track turns."
    )
    await s.op.confirm(TRACKS_OFF, "Both tracks must be off the ground.")
    async with s.test_caps(duty) as params:
        caps = {k: params[k].value for k in CAP_KEYS}
        async with s.mode(DriveMode.TEST, None):
            await s.dev.pump(0.2)
            left, right = (duty, 0.0) if side == "left" else (0.0, duty)
            msgs = await s.drive_for(left, right, secs)
    deltas = enc_delta(msgs)
    s.say("Encoder counts during the test:")
    for name, d in zip(ENC_NAMES, deltas, strict=True):
        s.say(f"  {name:12s} {d:+d}")
    moved = await s.op.choice(
        f"Which way did the {side} track run?", args.track_moved, ("forward", "backward", "none")
    )
    raw = {"side": side, "duty": duty, "secs": secs, "deltas": list(deltas),
           "track_moved": moved, "caps_before": caps}  # fmt: skip
    if moved == "none":
        s.log("motor-test", {**raw, "problem": "track did not move"})
        s.say(
            "The track did not move: check the battery switch, the driver's enable jumpers, the "
            "motor wires and the duty. STOP and ask before trying a higher duty."
        )
        return CHECK_FAILED
    physical = 1 if moved == "forward" else -1
    problems: list[str] = []
    other = [i for i in range(4) if i not in SIDE_ENC[side]]
    for i in other:
        if abs(deltas[i]) >= MIN_TICKS:
            problems.append(
                f"{ENC_NAMES[i]} counted {deltas[i]:+d} although that track was not driven "
                "(encoder cables swapped between sides?)"
            )
    motor_key = f"MOTOR_INVERT_{side.upper()}"
    cur_motor = int(params[motor_key].value)
    proposal: dict[str, float] = {motor_key: cur_motor if physical > 0 else 1 - cur_motor}
    for i in SIDE_ENC[side]:
        key = ENC_KEYS[i]
        cur = int(params[key].value)
        if abs(deltas[i]) < MIN_TICKS:
            problems.append(f"{ENC_NAMES[i]} did not count ({deltas[i]:+d}): power, A/B wires?")
            continue
        sign = 1 if deltas[i] > 0 else -1
        proposal[key] = cur if sign == physical else 1 - cur
    for p in problems:
        s.say(f"  PROBLEM: {p}")
    changed = {k: v for k, v in proposal.items() if int(params[k].value) != int(v)}
    s.say(
        "  Current values are right." if not changed
        else "  To change: " + ", ".join(f"{k} -> {int(v)}" for k, v in changed.items())
    )  # fmt: skip
    method = f"motor-test {side}: duty {duty:g}, {secs:g} s, track ran {moved}"
    await s.propose("motor-test", proposal, method=method, raw={**raw, "problems": problems})
    return CHECK_FAILED if problems else 0


async def run_encoder_check(s: Session, args: argparse.Namespace) -> int:
    s.say(
        "encoder-check: nothing is driven (MODE stays SAFE). Push each track FORWARD by hand, "
        "about one turn of the drive sprocket, when asked."
    )
    await s.dev.wait_telemetry()
    params = await s.read_params()
    rows: dict[str, list[int]] = {}
    problems: list[str] = []
    proposal: dict[str, float] = {}
    for side in ("left", "right"):
        msgs = await s.wait_operator(
            f"Push the {side.upper()} track forward by hand now.", label=f"push-{side}"
        )
        deltas = enc_delta(msgs)
        rows[side] = list(deltas)
        s.say(f"  {side} push: " + "  ".join(
            f"{n} {d:+d}" for n, d in zip(ENC_NAMES, deltas, strict=True)))  # fmt: skip
        for i in range(4):
            on_side = i in SIDE_ENC[side]
            d = deltas[i]
            if on_side and abs(d) < MIN_TICKS:
                problems.append(f"{ENC_NAMES[i]} did not count when the {side} track was pushed")
            elif on_side and d < 0:
                cur = int(params[ENC_KEYS[i]].value)
                proposal[ENC_KEYS[i]] = 1 - cur
                s.say(f"    {ENC_NAMES[i]} counts DOWN going forward: invert it")
            elif not on_side and abs(d) >= MIN_TICKS:
                problems.append(
                    f"{ENC_NAMES[i]} counted {d:+d} when the {side} track was pushed "
                    "(cables swapped between sides?)"
                )
    for p in problems:
        s.say(f"  PROBLEM: {p}")
    raw = {"pushes": rows, "problems": problems, "min_ticks": MIN_TICKS}
    if proposal and not problems:
        await s.propose("encoder-check", proposal, method="encoder-check: pushed by hand", raw=raw)
    else:
        s.log("encoder-check", raw)
        if not problems:
            s.say("encoder-check: every encoder counts up going forward")
    if problems:
        raise StepError("encoder-check found problems: fix the wiring first (BRINGUP.md 4)")
    return 0
