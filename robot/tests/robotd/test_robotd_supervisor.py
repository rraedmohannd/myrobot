"""Supervisor state machine: pure unit tests (facts and commands in, actions out)."""

from robotd.supervisor import Action, Facts, Supervisor


def kinds(actions: list[Action]) -> list[str]:
    return [a.kind for a in actions]


def make() -> Supervisor:
    sup = Supervisor(engage_timeout_s=10, reengage_cooldown_s=5, fault_clear_s=2)
    assert sup.update(Facts(now_s=0.0)) == []
    return sup


def roaming() -> Supervisor:
    sup = make()
    accepted, reason, actions = sup.command("start_roaming")
    assert accepted and reason is None and kinds(actions) == ["open_run", "recheck_path"]
    assert sup.state == "ROAMING" and sup.run_open and sup.may_move
    return sup


# ---------------- commands ----------------
def test_start_roaming_only_from_idle() -> None:
    sup = roaming()
    accepted, reason, actions = sup.command("start_roaming")
    assert not accepted and reason == "not idle (state ROAMING)" and actions == []


def test_pause_resume() -> None:
    sup = make()
    assert sup.command("pause")[:2] == (False, "nothing to pause (state IDLE)")
    assert sup.command("resume")[:2] == (False, "not paused")
    sup = roaming()
    accepted, _, actions = sup.command("pause")
    assert accepted and kinds(actions) == ["stop"] and sup.state == "PAUSED" and not sup.may_move
    accepted, _, actions = sup.command("resume")
    assert accepted and sup.state == "ROAMING" and kinds(actions) == ["recheck_path"]


def test_return_to_start_and_its_end() -> None:
    sup = make()
    assert sup.command("return_to_start")[:2] == (False, "not roaming (state IDLE)")
    sup = roaming()
    accepted, _, actions = sup.command("return_to_start")
    assert accepted and sup.state == "RETURNING" and kinds(actions) == ["start_return"]
    # pausing a return and asking again continues it
    sup.command("pause")
    accepted, _, actions = sup.command("return_to_start")
    assert accepted and sup.state == "RETURNING" and actions == []
    done = sup.return_finished("return_done", {"method": "direct"})
    assert kinds(done) == ["stop", "event", "end_run"] and done[1].data["kind"] == "return_done"
    assert sup.state == "IDLE" and not sup.run_open


def test_return_failed_reports_the_reason() -> None:
    sup = roaming()
    sup.command("return_to_start")
    out = sup.return_finished("return_failed", {"reason": "blocked"})
    assert out[1].data == {"kind": "return_failed", "data": {"reason": "blocked"}}
    assert sup.state == "IDLE"


def test_set_mode_and_unknown_command() -> None:
    sup = make()
    assert sup.command("set_mode", {"mode": "quiet"})[:2] == (True, None) and sup.mode == "quiet"
    assert sup.command("set_mode", {})[:2] == (False, "set_mode needs args.mode")
    assert sup.command("dance")[:2] == (False, "unknown command dance")


def test_refused_when_blocked_low_battery_or_fault() -> None:
    sup = make()
    sup.update(Facts(now_s=1.0, front_blocked=True))
    assert sup.command("start_roaming")[:2] == (False, "blocked: no free way in front")
    sup = make()
    sup.update(Facts(now_s=1.0, battery_low=True))
    assert sup.state == "FAULT"
    assert sup.command("start_roaming")[:2] == (False, "battery low")
    sup = make()
    sup.update(Facts(now_s=1.0, faults=["imu_fault"]))
    assert sup.command("start_roaming")[:2] == (False, "fault active: imu_fault")
    assert sup.command("set_mode", {"mode": "x"})[0]  # harmless commands still work


# ---------------- engagement ----------------
def test_engagement_stops_then_conversation_then_resume() -> None:
    sup = roaming()
    actions = sup.update(Facts(now_s=1.0, vision_engaged=True))
    assert sup.state == "ENGAGED" and kinds(actions) == ["stop", "engage"] and not sup.may_move
    assert sup.command("resume")[:2] == (False, "a conversation is in progress")
    actions = sup.update(Facts(now_s=2.0, vision_engaged=True, conversation_active=True))
    assert sup.state == "CONVERSATION"
    for cmd in ("start_roaming", "resume", "return_to_start"):
        assert sup.command(cmd)[:2] == (False, "a conversation is in progress")
    assert sup.command("pause")[:2] == (False, "nothing to pause (state CONVERSATION)")
    # conversation over: recheck the path, then go on roaming; no instant re-engagement
    actions = sup.update(Facts(now_s=30.0, vision_engaged=True))
    assert sup.state == "ROAMING" and kinds(actions) == ["recheck_path"]
    assert sup.update(Facts(now_s=33.0, vision_engaged=True)) == [] and sup.state == "ROAMING"
    sup.update(Facts(now_s=36.0, vision_engaged=True))
    assert sup.state == "ENGAGED"


def test_engaged_without_conversation_times_out() -> None:
    sup = make()
    sup.update(Facts(now_s=1.0, vision_engaged=True))
    assert sup.state == "ENGAGED"
    # still facing after the timeout: ask the page again
    assert kinds(sup.update(Facts(now_s=11.5, vision_engaged=True))) == ["engage"]
    sup.update(Facts(now_s=22.0, vision_engaged=False))
    assert sup.state == "IDLE"


def test_start_button_conversation_from_any_state_returns_to_it() -> None:
    sup = roaming()
    sup.command("return_to_start")
    actions = sup.update(Facts(now_s=1.0, conversation_active=True))
    assert sup.state == "CONVERSATION" and kinds(actions) == ["stop"]
    sup.update(Facts(now_s=9.0))
    assert sup.state == "RETURNING"


def test_no_engagement_while_paused_or_returning() -> None:
    sup = roaming()
    sup.command("pause")
    assert sup.update(Facts(now_s=1.0, vision_engaged=True)) == [] and sup.state == "PAUSED"


# ---------------- backend loss ----------------
def test_backend_loss_pauses_roaming_and_resumes_when_back() -> None:
    sup = roaming()
    actions = sup.update(Facts(now_s=1.0, backend_ok=False))
    assert sup.state == "PAUSED" and sup.pause_reason == "backend_lost"
    assert kinds(actions) == ["stop", "event"] and actions[1].data["kind"] == "comm_loss"
    assert sup.update(Facts(now_s=2.0, backend_ok=False, vision_engaged=True)) == []
    actions = sup.update(Facts(now_s=5.0))
    assert sup.state == "ROAMING" and kinds(actions) == ["recheck_path"]


def test_backend_loss_does_not_stop_a_return() -> None:
    sup = roaming()
    sup.command("return_to_start")
    assert sup.update(Facts(now_s=1.0, backend_ok=False)) == [] and sup.state == "RETURNING"


def test_backend_loss_without_auto_resume() -> None:
    sup = Supervisor(resume_after_backend_loss=False)
    sup.update(Facts(now_s=0.0))
    sup.command("start_roaming")
    sup.update(Facts(now_s=1.0, backend_ok=False))
    sup.update(Facts(now_s=2.0))
    assert sup.state == "PAUSED" and sup.command("resume")[0] and sup.state == "ROAMING"


# ---------------- faults ----------------
def test_fault_stops_and_never_resumes_motion_by_itself() -> None:
    sup = roaming()
    actions = sup.update(Facts(now_s=1.0, faults=["telemetry_lost"]))
    assert sup.state == "FAULT" and kinds(actions) == ["stop", "event"] and not sup.may_move
    assert sup.faults == ["telemetry_lost"]
    assert sup.command("resume")[:2] == (False, "fault active: telemetry_lost")
    sup.update(Facts(now_s=2.0))
    assert sup.state == "FAULT"  # must stay clear for fault_clear_s
    sup.update(Facts(now_s=4.5))
    assert sup.state == "PAUSED" and sup.pause_reason == "fault"
    assert sup.command("resume")[0] and sup.state == "ROAMING"


def test_fault_from_idle_goes_back_to_idle() -> None:
    sup = make()
    sup.update(Facts(now_s=1.0, faults=["encoder_fault"]))
    assert sup.state == "FAULT"
    sup.update(Facts(now_s=2.0))
    sup.update(Facts(now_s=4.5))
    assert sup.state == "IDLE"


def test_low_battery_is_a_fault() -> None:
    sup = roaming()
    sup.update(Facts(now_s=1.0, battery_low=True))
    assert sup.state == "FAULT" and sup.faults == ["low_battery"]
