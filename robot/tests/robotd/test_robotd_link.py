"""LinkCore: DRIVE / HEARTBEAT rhythm, counters, telemetry timeout (no I/O)."""

from scr_serial import Drive, Flags, FrameDecoder, Heartbeat, Stop, Telemetry, encode_frame

from robotd.link import LinkCore


def sent(core: LinkCore, seconds: float, start: float = 0.0) -> list:
    decoder, out = FrameDecoder(), []
    for i in range(int(seconds * 100)):
        out += [f.msg for f in decoder.feed(core.poll(start + i * 0.01))]
    assert decoder.bad_frames == 0 and decoder.lost_frames == 0
    return out


def telemetry(t_ms: int) -> Telemetry:
    return Telemetry(
        t_ms, (0, 0, 0, 0), 0.0, (0, 0, 1000), (0xFFFF,) * 4, 11800, 400, Flags.SIMULATED
    )


def test_heartbeat_at_10_hz_when_not_moving() -> None:
    msgs = sent(LinkCore(), 1.0)
    assert len(msgs) == 10 and all(isinstance(m, Heartbeat) for m in msgs)


def test_drive_at_20_hz_while_moving_then_one_stop() -> None:
    core = LinkCore()
    core.set_drive(0.2, 0.1)
    msgs = sent(core, 1.0)
    assert len(msgs) == 20 and all(m == Drive(0.2, 0.1) for m in msgs)
    core.set_drive(0.0, 0.0)
    after = sent(core, 1.0, start=1.0)
    assert after[0] == Stop() and sum(isinstance(m, Stop) for m in after) == 1
    assert sum(isinstance(m, Heartbeat) for m in after) == 10
    assert not any(isinstance(m, Drive) for m in after)


def test_bad_and_lost_frames_are_counted() -> None:
    core = LinkCore()
    good = [encode_frame(telemetry(i * 20), i) for i in range(6)]
    damaged = bytearray(good[2])
    damaged[3] ^= 0x55
    data = good[0] + good[1] + bytes(damaged) + good[4] + good[5]  # 2 damaged, 3 missing
    msgs = core.feed(data, 1.0)
    assert len(msgs) == 4
    assert core.bad_frames == 1 and core.lost_frames == 2
    assert core.stats()["received"] == 4


def test_telemetry_timeout() -> None:
    core = LinkCore(telemetry_timeout_ms=500)
    assert core.telemetry_lost(0.0)  # nothing received yet
    core.feed(encode_frame(telemetry(0), 0), 1.0)
    assert not core.telemetry_lost(1.4)
    assert core.telemetry_lost(1.6)


def test_reset_after_reconnect_forgets_commands_but_keeps_counters() -> None:
    core = LinkCore()
    core.feed(b"\x01\x02\x03\x00", 0.0)
    core.set_drive(0.2, 0.2)
    core.reset_stream()
    assert core.bad_frames == 1 and core.reconnects == 1
    assert not core.moving and core.telemetry_lost(0.0)
