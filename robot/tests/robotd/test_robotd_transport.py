"""Transports: pyserial (through its loop:// test port, no hardware) and error handling."""

import pytest
from scr_serial import FrameDecoder, Heartbeat, encode_frame

from robotd.config import Config, load_config
from robotd.transport import SerialTransport, TcpTransport, TransportError


async def test_serial_transport_round_trip_on_pyserial_loopback() -> None:
    port = SerialTransport("loop://", 460800)
    await port.open()
    try:
        await port.write(encode_frame(Heartbeat(), 9))
        data = b""
        for _ in range(20):
            data += await port.read()
            if data.endswith(b"\x00"):
                break
        frames = FrameDecoder().feed(data)
        assert len(frames) == 1 and frames[0].seq == 9 and frames[0].msg == Heartbeat()
    finally:
        await port.close()
    with pytest.raises(TransportError):
        await port.read()


async def test_missing_ports_raise_transport_error() -> None:
    with pytest.raises(TransportError):
        await SerialTransport("/dev/does-not-exist-esp32").open()
    with pytest.raises(TransportError):
        await TcpTransport("127.0.0.1", 1).open()


def test_config_sources_and_token_is_not_logged(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "robotd.json"
    path.write_text('{"roam_speed_mps": 0.1, "serial_port": "COM7"}', encoding="utf-8")
    env = {
        "BACKEND_URL": "wss://lab.example:8443/ws/robot",
        "ROBOT_TOKEN": "abc",
        "ROBOTD_UI_PORT": "9000",
    }
    cfg = load_config(str(path), env=env, sim=True, serial_port=None)
    assert (cfg.roam_speed_mps, cfg.serial_port, cfg.ui_port, cfg.sim) == (0.1, "COM7", 9000, True)
    assert (
        cfg.backend_http == "https://lab.example:8443"
        and cfg.page_backend_host == "lab.example:8443"
    )
    from robotd.config import public_dict

    assert public_dict(cfg)["robot_token"] == "set" and "abc" not in str(public_dict(cfg))
    assert Config().odom_config(True)["calibrated"] is False
    with pytest.raises(ValueError):
        path.write_text('{"nonsense": 1}', encoding="utf-8")
        load_config(str(path), env={})
