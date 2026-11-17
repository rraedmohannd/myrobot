// Sprint 5 firmware features against the STUB drivers (no hardware, nothing here is a physical
// result): runtime parameters + NVS, motor voltage compensation, diagnostics, slip, bring-up
// modes, battery cut-off, PCNT wrap, I2C recovery event, the CI pin build's permanent SAFE.
#include <unity.h>

#include <cmath>
#include <vector>

#include "config.h"
#include "scr_app.h"
#include "scr_params.h"
#include "scr_stub_drivers.h"
#include "scr_version.h"

using namespace scr;
using namespace scr::proto;

void setUp() {}
void tearDown() {}

// Same test values as test_app (TEST VALUES, not calibration data).
static AppConfig testConfig() {
  AppConfig c;
  c.pid.kp = 1.0f;
  c.pid.ki = 5.0f;
  c.pid.kff = 2.5f;
  c.metres_per_tick = 0.0001f;
  c.ramp_mm_s2 = 500.0f;
  c.max_speed_mm_s = 300;
  c.test_full_scale_mm_s = 1000.0f;
  c.test_max_duty = 0.25f;
  c.servo.min_cdeg = -6000;
  c.servo.max_cdeg = 6000;
  c.servo.slew_cdeg_per_s = 9000.0f;
  c.servo.us_per_deg = 10.0f;
  c.servo.pulse_min_us = 800;
  c.servo.pulse_max_us = 2200;
  c.gyro_cal.samples = 200;
  c.gyro_cal.max_spread_urad_s = 20000;
  c.battery.low_mv = 10000;
  c.battery.recover_mv = 10500;
  c.battery.hold_ms = 500;
  c.encoder_fault.min_duty = 0.2f;
  c.encoder_fault.timeout_ms = 500;
  return c;
}

struct Rig {
  stub::StubRobot robot;
  FirmwareApp* app = nullptr;
  uint32_t now = 0;
  uint8_t seq = 0;
  bool keepalive = true;
  int slow_encoder = -1;  // this encoder counts only a third (simulated slip)
  FrameDecoder dec;
  std::vector<Message> rx;  // every frame the firmware sent
  Telemetry tel{};
  AppConfig cfg;

  explicit Rig(const AppConfig& c = testConfig()) : cfg(c) { boot(); }
  ~Rig() { delete app; }
  Rig(const Rig&) = delete;

  // (Re)boot: a new application on the same stub hardware (the stub NVS keeps its content).
  void boot() {
    delete app;
    app = new FirmwareApp(cfg, robot.hardware());
    app->begin(now);
    run(300);
  }
  uint8_t send(const Message& m) {
    uint8_t buf[kMaxFrame];
    const size_t n = encodeFrame(m, seq, buf, sizeof(buf));
    robot.link.inject(buf, n);
    return seq++;
  }
  void step() {
    now += 10;
    if (keepalive && now % 100 == 0) send(makeHeartbeat());
    robot.step(0.01f);
    if (slow_encoder >= 0) robot.encoders.counts[slow_encoder] /= 3;
    app->controlTick(now);
    if (now % 20 == 0) app->telemetryTick(now);
    for (uint8_t b : robot.link.tx) {
      if (dec.feed(b) == FrameDecoder::Feed::Frame) {
        rx.push_back(dec.message());
        if (dec.message().type == MsgType::Telemetry) tel = dec.message().telemetry;
      }
    }
    robot.link.tx.clear();
  }
  void run(uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) step();
  }
  int count(MsgType t) const {
    int n = 0;
    for (const Message& m : rx) n += m.type == t ? 1 : 0;
    return n;
  }
  int events(uint8_t code, int16_t* arg = nullptr) const {
    int n = 0;
    for (const Message& m : rx) {
      if (m.type == MsgType::Event && m.event.code == code) {
        ++n;
        if (arg) *arg = m.event.arg;
      }
    }
    return n;
  }
  int ackFor(MsgType type, uint8_t s) const {
    for (const Message& m : rx) {
      if (m.type == MsgType::Ack && m.ack.acked_type == static_cast<uint8_t>(type) && m.ack.acked_seq == s) {
        return m.ack.status;
      }
    }
    return -1;
  }
  // Index in rx of the first frame of a type after position `from`; -1 when none.
  int find(MsgType t, size_t from = 0) const {
    for (size_t i = from; i < rx.size(); ++i) {
      if (rx[i].type == t) return static_cast<int>(i);
    }
    return -1;
  }
  int mode(uint8_t m) {
    const uint8_t s = send(makeMode(m));
    step();
    return ackFor(MsgType::Mode, s);
  }
  int paramSet(uint16_t id, float v) {
    const uint8_t s = send(makeParamSet(id, v));
    step();
    return ackFor(MsgType::ParamSet, s);
  }
  void drive(int16_t l, int16_t r, uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) {
      if (t % 50 == 0) send(makeDrive(l, r));
      step();
    }
  }
};

// ================================ parameter store =============================================
static void test_default_hash_equals_python() {
  // shared/scr_serial: hex(param_hash(load_table().defaults())) printed 0x8522fadf on 2026-11-17.
  // If params.json changes, this value changes: recompute it with the Python command in README.
  TEST_ASSERT_EQUAL_HEX32(0x8522FADFu, defaultParamHash());
  ParamStore s;
  TEST_ASSERT_EQUAL_HEX32(defaultParamHash(), s.hash());
  TEST_ASSERT_FALSE(s.unsaved());
  TEST_ASSERT_FALSE(s.anyFromNvs());
}

static void test_shipped_config_seeds_exactly_the_table_defaults() {
  // cfg::makeAppConfig() is what the ESP32 runs: its parameters must be the params.json defaults.
  ParamStore p;
  TEST_ASSERT_EQUAL_INT(0, seedParamsFromConfig(cfg::makeAppConfig(), &p));
  TEST_ASSERT_EQUAL_HEX32(defaultParamHash(), p.hash());
  stub::StubRobot robot;
  FirmwareApp app(cfg::makeAppConfig(), robot.hardware());
  TEST_ASSERT_EQUAL_INT(0, app.seedFailures());
  TEST_ASSERT_EQUAL_HEX32(defaultParamHash(), app.params().hash());
  TEST_ASSERT_EQUAL_FLOAT(0.0f, app.config().motor_max_duty);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, app.config().metres_per_tick);
}

static void test_bounds_types_and_unknown_ids() {
  ParamStore s;
  TEST_ASSERT_EQUAL_INT(kAckUnknownParam, ParamStore::check(0, 1.0f));
  TEST_ASSERT_EQUAL_INT(kAckUnknownParam, ParamStore::check(41, 1.0f));
  TEST_ASSERT_EQUAL_INT(kAckUnknownParam, ParamStore::check(0xFFFF, 1.0f));
  // OBSTACLE_THRESHOLD_MM: int, 150..1000
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::OBSTACLE_THRESHOLD_MM, 149.0f));
  TEST_ASSERT_EQUAL_INT(kAckOk, ParamStore::check(params::OBSTACLE_THRESHOLD_MM, 150.0f));
  TEST_ASSERT_EQUAL_INT(kAckOk, ParamStore::check(params::OBSTACLE_THRESHOLD_MM, 1000.0f));
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::OBSTACLE_THRESHOLD_MM, 1000.5f));
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::OBSTACLE_THRESHOLD_MM, 300.5f));
  // bool: 0 or 1 only
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::MOTOR_INVERT_LEFT, 0.5f));
  TEST_ASSERT_EQUAL_INT(kAckOk, ParamStore::check(params::MOTOR_INVERT_LEFT, 1.0f));
  // float with NaN / inf
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::PID_KP, NAN));
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::PID_KP, INFINITY));
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, ParamStore::check(params::MOTOR_MAX_DUTY, 1.0001f));
  // set refuses and leaves the value unchanged
  const uint32_t h = s.hash();
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, s.set(params::OBSTACLE_THRESHOLD_MM, 100.0f));
  TEST_ASSERT_EQUAL_FLOAT(250.0f, s.get(params::OBSTACLE_THRESHOLD_MM));
  TEST_ASSERT_EQUAL_HEX32(h, s.hash());
  bool changed = false;
  TEST_ASSERT_EQUAL_INT(kAckOk, s.set(params::OBSTACLE_THRESHOLD_MM, 300.0f, &changed));
  TEST_ASSERT_TRUE(changed);
  TEST_ASSERT_NOT_EQUAL(h, s.hash());
  TEST_ASSERT_TRUE(s.unsaved());
  TEST_ASSERT_EQUAL_INT(kParamSourceSet, s.sourceAt(ParamStore::indexOf(params::OBSTACLE_THRESHOLD_MM)));
  // the hash follows the A4 definition (manual FNV over every parameter)
  uint32_t manual = kFnvOffset;
  for (int i = 0; i < ParamStore::count(); ++i) manual = fnv1aParam(manual, ParamStore::def(i).id, s.valueAt(i));
  TEST_ASSERT_EQUAL_HEX32(manual, s.hash());
}

static void test_save_reboot_reload_and_bad_nvs_values() {
  stub::StubStorage nvs;
  {
    ParamStore s;
    TEST_ASSERT_EQUAL_INT(0, s.load(&nvs));  // empty NVS: defaults
    s.set(params::PID_KP, 1.5f);
    s.set(params::METRES_PER_TICK, 0.0005f);
    TEST_ASSERT_TRUE(s.unsaved());
    TEST_ASSERT_TRUE(s.save(&nvs));
    TEST_ASSERT_FALSE(s.unsaved());
  }
  // "reboot": a new store reads the same NVS
  ParamStore r;
  TEST_ASSERT_EQUAL_INT(ParamStore::count(), r.load(&nvs));
  TEST_ASSERT_EQUAL_FLOAT(1.5f, r.get(params::PID_KP));
  TEST_ASSERT_EQUAL_FLOAT(0.0005f, r.get(params::METRES_PER_TICK));
  TEST_ASSERT_TRUE(r.anyFromNvs());
  TEST_ASSERT_FALSE(r.unsaved());
  TEST_ASSERT_EQUAL_INT(kParamSourceNvs, r.sourceAt(ParamStore::indexOf(params::PID_KP)));
  // A stored value outside the compiled bounds (damaged flash, table changed) is ignored.
  nvs.committed[params::OBSTACLE_THRESHOLD_MM] = 20.0f;
  nvs.committed[params::MOTOR_MAX_DUTY] = NAN;
  ParamStore b;
  b.load(&nvs);
  TEST_ASSERT_EQUAL_INT(2, b.rejectedOnLoad());
  TEST_ASSERT_EQUAL_FLOAT(250.0f, b.get(params::OBSTACLE_THRESHOLD_MM));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, b.get(params::MOTOR_MAX_DUTY));
  // A failing storage write leaves everything unsaved.
  nvs.write_result = false;
  b.set(params::PID_KI, 2.0f);
  TEST_ASSERT_FALSE(b.save(&nvs));
  TEST_ASSERT_TRUE(b.unsaved());
}

// ================================ parameters in the app =======================================
static void test_param_set_ok_reports_and_applies() {
  Rig rig;
  const size_t from = rig.rx.size();
  const uint8_t s = rig.send(makeParamSet(params::OBSTACLE_THRESHOLD_MM, 400.0f));
  rig.step();
  const int ack = rig.find(MsgType::Ack, from);
  const int rep = rig.find(MsgType::ParamReport, from);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSet, s));
  TEST_ASSERT_TRUE(ack >= 0 && rep > ack);  // ACK first, then PARAM_REPORT
  const ParamReport& r = rig.rx[rep].param_report;
  TEST_ASSERT_EQUAL_UINT16(params::OBSTACLE_THRESHOLD_MM, r.id);
  TEST_ASSERT_EQUAL_FLOAT(400.0f, r.value);
  TEST_ASSERT_EQUAL_FLOAT(150.0f, r.min);
  TEST_ASSERT_EQUAL_FLOAT(1000.0f, r.max);
  TEST_ASSERT_EQUAL_UINT8(kParamSourceSet, r.source);
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventParamChanged, &arg));
  TEST_ASSERT_EQUAL_INT16(params::OBSTACLE_THRESHOLD_MM, arg);
  TEST_ASSERT_EQUAL_UINT16(400, rig.app->config().safety.obstacle_mm);
  rig.run(40);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagParamsUnsaved);
  TEST_ASSERT_EQUAL_HEX32(rig.app->params().hash(), rig.tel.param_hash);
  // The new threshold is used by the local obstacle stop: 350 mm now blocks forward.
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.robot.ultrasonic.setDistanceMm(1, 350.0f);
  rig.run(200);
  const uint8_t d = rig.send(makeDrive(100, 100));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Drive, d));
}

static void test_param_set_rejections_leave_value_unchanged() {
  Rig rig;
  const uint32_t h = rig.app->params().hash();
  TEST_ASSERT_EQUAL_INT(kAckOutOfRange, rig.paramSet(params::OBSTACLE_THRESHOLD_MM, 100.0f));
  TEST_ASSERT_EQUAL_INT(kAckUnknownParam, rig.paramSet(999, 1.0f));
  TEST_ASSERT_EQUAL_HEX32(h, rig.app->params().hash());
  TEST_ASSERT_EQUAL_INT(0, rig.count(MsgType::ParamReport));
  TEST_ASSERT_EQUAL_INT(0, rig.events(kEventParamChanged));
}

static void test_param_set_busy_while_moving() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.drive(200, 200, 500);
  const uint32_t h = rig.app->params().hash();
  const uint8_t s = rig.send(makeParamSet(params::PID_KP, 2.0f));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBusy, rig.ackFor(MsgType::ParamSet, s));
  TEST_ASSERT_EQUAL_HEX32(h, rig.app->params().hash());
  // Stop: targets zero, but the tracks still roll down -> still BUSY until the speed is low.
  rig.send(makeStop());
  rig.run(1000);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::PID_KP, 2.0f));
}

static void test_param_get_all_and_one() {
  Rig rig;
  const uint8_t s = rig.send(makeParamGet(kParamAll));
  rig.step();
  TEST_ASSERT_EQUAL_INT(ParamStore::count(), rig.count(MsgType::ParamReport));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamGet, s));
  // id order, ACK after the last report
  int last_id = 0;
  for (const Message& m : rig.rx) {
    if (m.type == MsgType::ParamReport) {
      TEST_ASSERT_TRUE(m.param_report.id > last_id);
      last_id = m.param_report.id;
    }
  }
  const uint8_t u = rig.send(makeParamGet(77));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckUnknownParam, rig.ackFor(MsgType::ParamGet, u));
}

static void test_param_save_rules_and_reload_after_reboot() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::PID_KP, 3.0f));
  // driving: REJECTED_MODE
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.drive(150, 150, 300);
  uint8_t s = rig.send(makeParamSave());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::ParamSave, s));
  // idle in DRIVE (targets 0, tracks still): OK
  rig.send(makeStop());
  rig.run(1500);
  s = rig.send(makeParamSave());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSave, s));
  rig.run(40);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagParamsUnsaved);
  const uint32_t h = rig.app->params().hash();
  // reboot on the same NVS: same values, same hash, PARAMS_FROM_NVS in DIAG
  rig.rx.clear();
  rig.boot();
  TEST_ASSERT_EQUAL_FLOAT(3.0f, rig.app->params().get(params::PID_KP));
  TEST_ASSERT_EQUAL_HEX32(h, rig.app->params().hash());
  const int d = rig.find(MsgType::DiagReport);
  TEST_ASSERT_TRUE(d >= 0);
  TEST_ASSERT_TRUE(rig.rx[d].diag_report.diag_flags & kDiagParamsFromNvs);
  TEST_ASSERT_EQUAL_HEX32(h, rig.rx[d].diag_report.param_hash);
  // SAFE mode: always allowed; failing storage -> BUSY
  rig.robot.storage.write_result = false;
  s = rig.send(makeParamSave());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBusy, rig.ackFor(MsgType::ParamSave, s));
}

static void test_uncalibrating_metres_per_tick_in_drive_goes_safe() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::METRES_PER_TICK, 0.0f));
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, rig.app->safety().mode());
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.mode(kModeDrive));
}

static void test_motor_invert_is_applied_at_the_output() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::MOTOR_INVERT_LEFT, 1.0f));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  rig.drive(200, 200, 600);
  TEST_ASSERT_TRUE(rig.app->dutyLeft() > 0.1f);         // logical: forward
  TEST_ASSERT_TRUE(rig.robot.motors.left < -0.1f);      // physical output inverted
  TEST_ASSERT_TRUE(rig.robot.motors.right > 0.1f);
}

// ================================ voltage compensation (A8) ===================================
static VoltageCompConfig vc(float max_duty) {
  VoltageCompConfig c;
  c.motor_max_duty = max_duty;
  c.motor_rated_v = 7.2f;
  c.v_driver_drop = 2.0f;
  return c;
}

static void test_vcomp_full_and_half_pack() {
  VoltageCompensator v(vc(1.0f));
  v.update(12.6f, true, 0.1f);
  TEST_ASSERT_FALSE(v.fallback());
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 7.2f / 10.6f, v.dutyCap());
  VoltageCompensator h(vc(1.0f));
  h.update(9.0f, true, 0.1f);  // a low pack: 7.2 / 7.0 > 1 -> the absolute cap
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, h.dutyCap());
  VoltageCompensator m(vc(0.9f));
  m.update(9.0f, true, 0.1f);
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.9f, m.dutyCap());  // MOTOR_MAX_DUTY wins
  VoltageCompensator z(vc(0.0f));
  z.update(12.0f, true, 0.1f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, z.dutyCap());  // shipped default: nothing moves
}

static void test_vcomp_ina219_missing_uses_full_pack_constant() {
  VoltageCompensator v(vc(1.0f));
  TEST_ASSERT_TRUE(v.fallback());  // before any reading
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 7.2f / 10.6f, v.dutyCap());
  v.update(11.0f, true, 0.1f);
  v.update(0.0f, false, 0.1f);  // reading lost
  TEST_ASSERT_TRUE(v.fallback());
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 7.2f / 10.6f, v.dutyCap());
  v.update(1.2f, true, 0.1f);  // implausible (USB power only): fallback too
  TEST_ASSERT_TRUE(v.fallback());
  // In the app: no INA219 -> flag 13 in the telemetry.
  AppConfig c = testConfig();
  Rig rig(c);
  rig.robot.power.begin_result = false;
  rig.boot();
  rig.run(200);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagVoltageCompFallback);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 7.2f / 10.6f, rig.app->dutyCap());
}

static void test_vcomp_voltage_jumps_never_exceed_the_cap() {
  VoltageCompensator v(vc(0.8f));
  v.update(12.0f, true, 0.1f);
  const float cap_12 = v.dutyCap();
  // A sag under load (12.0 -> 8.0 V) raises the cap only slowly ...
  v.update(8.0f, true, 0.1f);
  TEST_ASSERT_TRUE(v.dutyCap() < cap_12 + 0.02f);
  TEST_ASSERT_TRUE(v.filteredVolts() > 11.5f);
  // ... a rise is followed at once (lower cap immediately).
  v.update(12.6f, true, 0.1f);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 7.2f / 10.6f, v.dutyCap());
  // Jumping around for a long time: never above the absolute cap, never NaN.
  for (int i = 0; i < 1000; ++i) {
    v.update((i % 7 == 0) ? 3.5f : ((i % 3 == 0) ? 13.0f : 6.0f), i % 11 != 0, 0.1f);
    const float cap = v.dutyCap();
    TEST_ASSERT_FALSE(std::isnan(cap));
    TEST_ASSERT_TRUE(cap >= 0.0f && cap <= 0.8f);
  }
}

static void test_vcomp_caps_the_motor_duty_in_both_modes() {
  AppConfig c = testConfig();
  c.motor_max_duty = 1.0f;
  c.test_max_duty = 0.5f;
  c.max_speed_mm_s = 600;
  Rig rig(c);
  rig.robot.power.mv = 12600;  // full pack: cap = 7.2 / 10.6 = 0.679
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  rig.drive(1000, -1000, 3000);  // asks for duty 0.6 (max speed 600) open loop
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.5f, rig.robot.motors.left);  // TEST_MAX_DUTY below the cap
  // DRIVE: ask for far more speed than the plant gives at the cap
  rig.send(makeStop());
  rig.run(1500);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::MOTOR_RATED_V, 3.0f));  // cap = 3.0/10.6
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.drive(300, 300, 3000);
  TEST_ASSERT_TRUE(rig.robot.motors.left <= 3.0f / 10.6f + 1e-4f);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.2f);
}

// ================================ diagnostics =================================================
static void test_diag_at_boot_after_boot_event_and_on_request() {
  Rig rig;  // booted in the constructor
  const int boot = [&] {
    for (size_t i = 0; i < rig.rx.size(); ++i) {
      if (rig.rx[i].type == MsgType::Event && rig.rx[i].event.code == kEventBoot) return static_cast<int>(i);
    }
    return -1;
  }();
  const int diag = rig.find(MsgType::DiagReport);
  TEST_ASSERT_TRUE(boot >= 0 && diag > boot);
  TEST_ASSERT_EQUAL_INT(1, rig.count(MsgType::DiagReport));
  rig.robot.system.i2c_found = {0x20, 0x40, 0x68};
  rig.robot.system.reset_reason = kResetBrownout;
  rig.robot.system.expander = true;
  rig.robot.system.scan_done = false;  // the scan takes a while (another task)
  const int requests = rig.robot.system.scan_requests;
  rig.send(makeDiagRequest());
  rig.run(50);
  TEST_ASSERT_EQUAL_INT(requests + 1, rig.robot.system.scan_requests);
  TEST_ASSERT_EQUAL_INT(1, rig.count(MsgType::DiagReport));  // still waiting for the scan
  rig.robot.system.scan_done = true;
  rig.step();
  TEST_ASSERT_EQUAL_INT(2, rig.count(MsgType::DiagReport));
  const DiagReport& d = rig.rx[rig.find(MsgType::DiagReport, diag + 1)].diag_report;
  TEST_ASSERT_EQUAL_UINT8(SCR_FW_VERSION_MAJOR, d.fw_major);
  TEST_ASSERT_EQUAL_UINT8(2, d.fw_major);
  TEST_ASSERT_EQUAL_UINT8(kBuildNativeTest, d.build_env);
  TEST_ASSERT_EQUAL_UINT8(kResetBrownout, d.reset_reason);
  TEST_ASSERT_EQUAL_UINT8(3, d.i2c_count);
  TEST_ASSERT_EQUAL_HEX8(0x68, d.i2c_addr[2]);
  TEST_ASSERT_EQUAL_HEX8(0x00, d.i2c_addr[3]);
  TEST_ASSERT_EQUAL_HEX32(rig.app->params().hash(), d.param_hash);
  TEST_ASSERT_TRUE(d.sensors & kSensorImu);
  TEST_ASSERT_TRUE(d.sensors & kSensorIna219);
  TEST_ASSERT_TRUE(d.sensors & kSensorExpander);
  TEST_ASSERT_EQUAL_UINT32(rig.now, d.uptime_ms);
  TEST_ASSERT_EQUAL_UINT32(123456, d.free_heap);
}

static void test_diag_does_not_wait_for_ever_for_the_scan() {
  Rig rig;
  rig.robot.system.scan_done = false;
  rig.send(makeDiagRequest());
  rig.run(400);  // longer than diag_scan_wait_ms
  TEST_ASSERT_EQUAL_INT(2, rig.count(MsgType::DiagReport));
}

static void test_ultrasonic_presence_bits_in_diag() {
  Rig rig;
  rig.robot.ultrasonic.setDead(2);
  rig.rx.clear();
  rig.robot.ultrasonic.is_present[3] = false;
  rig.boot();
  const DiagReport& d = rig.rx[rig.find(MsgType::DiagReport)].diag_report;
  (void)d;
  rig.send(makeDiagRequest());
  rig.run(30);
  int last = -1;
  for (size_t i = 0; i < rig.rx.size(); ++i) {
    if (rig.rx[i].type == MsgType::DiagReport) last = static_cast<int>(i);
  }
  const uint8_t s = rig.rx[last].diag_report.sensors;
  TEST_ASSERT_TRUE(s & (kSensorUs0 << 0));
  TEST_ASSERT_TRUE(s & (kSensorUs0 << 1));
  TEST_ASSERT_FALSE(s & (kSensorUs0 << 2));  // never answered
  TEST_ASSERT_FALSE(s & (kSensorUs0 << 3));  // not wired
}

// ================================ robustness ==================================================
static void test_pcnt_wrap_at_both_ends() {
  const int16_t L = 32767;
  TEST_ASSERT_EQUAL_INT32(5, pcntDelta(100, 105, L));
  TEST_ASSERT_EQUAL_INT32(-5, pcntDelta(100, 95, L));
  // upward through +limit: 32765 -> (32766, 32767=0) -> 2  = 4 ticks
  TEST_ASSERT_EQUAL_INT32(4, pcntDelta(32765, 2, L));
  // downward through -limit: -32765 -> (-32766, -32767=0) -> -2  = -4 ticks
  TEST_ASSERT_EQUAL_INT32(-4, pcntDelta(-32765, -2, L));
  // from 0 after a reset at the limit, going down
  TEST_ASSERT_EQUAL_INT32(-3, pcntDelta(0, -3, L));
  // a long run accumulates exactly, through many wraps, in both directions
  int16_t hw = 0;
  int32_t total = 0;
  int16_t last = 0;
  for (int i = 0; i < 100000; ++i) {  // 100000 reads of +700 ticks
    int32_t v = hw + 700;
    if (v >= L) v -= L;
    hw = static_cast<int16_t>(v);
    total = wrapAdd(total, pcntDelta(last, hw, L));
    last = hw;
  }
  TEST_ASSERT_EQUAL_INT32(70000000, total);
  for (int i = 0; i < 100000; ++i) {
    int32_t v = hw - 900;
    if (v <= -L) v += L;
    hw = static_cast<int16_t>(v);
    total = wrapAdd(total, pcntDelta(last, hw, L));
    last = hw;
  }
  TEST_ASSERT_EQUAL_INT32(70000000 - 90000000, total);
  // int32 wrap of the cumulative count is defined (two's complement), no UB
  TEST_ASSERT_EQUAL_INT32(INT32_MIN, wrapAdd(INT32_MAX, 1));
  TEST_ASSERT_EQUAL_INT32(INT32_MAX, wrapAdd(INT32_MIN, -1));
}

static void test_i2c_recovery_is_reported_once() {
  Rig rig;
  rig.robot.system.recovered_attempts = 2;
  rig.run(50);
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventI2cRecovered, &arg));
  TEST_ASSERT_EQUAL_INT16(2, arg);
}

static void test_soft_task_watchdog_model() {
  SoftTaskWatchdog w(500);
  const int control = w.add(0);
  const int telemetry = w.add(0);
  for (uint32_t t = 10; t <= 1000; t += 10) {
    w.feed(control, t);
    if (t < 300) w.feed(telemetry, t);
    if (t < 790) TEST_ASSERT_EQUAL_INT(-1, w.expired(t));
  }
  TEST_ASSERT_EQUAL_INT(telemetry, w.expired(1000));
}

// ================================ slip (A5) ===================================================
static void test_slip_detector() {
  SlipConfig c;
  c.ratio = 0.25f;
  c.min_ticks = 30;
  SlipDetector s(c);
  // equal front/rear: no slip
  for (uint32_t t = 10; t <= 1000; t += 10) TEST_ASSERT_FALSE(s.update(t, 10, 10));
  // rear 50 % of front, above min ticks: one event, then quiet while it lasts
  int n = 0;
  for (uint32_t t = 1010; t <= 3000; t += 10) n += s.update(t, 10, 5) ? 1 : 0;
  TEST_ASSERT_EQUAL_INT(1, n);
  TEST_ASSERT_TRUE(s.slipping());
  // back to normal for a window: re-armed, then a new episode gives a new event
  for (uint32_t t = 3010; t <= 3600; t += 10) TEST_ASSERT_FALSE(s.update(t, 10, 10));
  TEST_ASSERT_FALSE(s.slipping());
  n = 0;
  for (uint32_t t = 3610; t <= 4500; t += 10) n += s.update(t, -10, -2) ? 1 : 0;
  TEST_ASSERT_EQUAL_INT(1, n);
  // below SLIP_MIN_TICKS nothing is checked (creeping)
  SlipDetector q(c);
  for (uint32_t t = 10; t <= 3000; t += 10) TEST_ASSERT_FALSE(q.update(t, (t % 100 == 0) ? 1 : 0, 0));
}

static void test_slip_event_from_the_app() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  // The right rear encoder counts only a third: the right side slips.
  rig.slow_encoder = 3;
  rig.drive(200, 200, 2000);
  int16_t side = -1;
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventSlip, &side));
  TEST_ASSERT_EQUAL_INT16(1, side);
}

// ================================ bring-up (A2 BRINGUP) =======================================
static void test_bringup_refused_outside_test_mode() {
  Rig rig;
  const uint8_t a = rig.send(makeBringup(kBringupImuRaw, 50));
  const uint8_t b = rig.send(makeBringup(kBringupUsRaw, 0));
  const uint8_t c = rig.send(makeBringup(kBringupServoJog, 10));
  const uint8_t off = rig.send(makeBringup(kBringupOff, 0));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Bringup, a));
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Bringup, b));
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Bringup, c));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Bringup, off));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  const uint8_t d = rig.send(makeBringup(kBringupImuRaw, 50));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Bringup, d));
  TEST_ASSERT_FALSE(rig.app->bringupActive());
}

static void test_bringup_imu_and_us_streams() {
  Rig rig;
  rig.robot.imu.sample.gyro_urad_s[0] = 11;
  rig.robot.imu.sample.gyro_urad_s[1] = -22;
  rig.robot.imu.sample.gyro_urad_s[2] = 333;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  const uint8_t bad = rig.send(makeBringup(kBringupImuRaw, 0));
  const uint8_t bad2 = rig.send(makeBringup(kBringupImuRaw, 201));
  const uint8_t ok = rig.send(makeBringup(kBringupImuRaw, 50));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBadArgument, rig.ackFor(MsgType::Bringup, bad));
  TEST_ASSERT_EQUAL_INT(kAckBadArgument, rig.ackFor(MsgType::Bringup, bad2));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Bringup, ok));
  const int before = rig.count(MsgType::ImuRaw);
  rig.run(1000);
  const int n = rig.count(MsgType::ImuRaw) - before;
  TEST_ASSERT_INT_WITHIN(1, 50, n);  // 50 Hz
  const ImuRaw& r = rig.rx[rig.find(MsgType::ImuRaw)].imu_raw;
  TEST_ASSERT_EQUAL_INT32(333, r.gyro_urad_s[2]);  // bias not removed, no scale
  TEST_ASSERT_EQUAL_INT32(-22, r.gyro_urad_s[1]);
  rig.run(40);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagBringupActive);
  // US_RAW: one per ultrasonic round (4 slots of 30 ms = 120 ms)
  const uint8_t us = rig.send(makeBringup(kBringupUsRaw, 0));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Bringup, us));
  const int us_before = rig.count(MsgType::UsRaw);
  rig.run(1200);
  TEST_ASSERT_INT_WITHIN(1, 10, rig.count(MsgType::UsRaw) - us_before);
  // leaving TEST mode ends every stream
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeSafe));
  rig.step();
  TEST_ASSERT_FALSE(rig.app->bringupActive());
  const int frozen = rig.count(MsgType::ImuRaw) + rig.count(MsgType::UsRaw);
  rig.run(500);
  TEST_ASSERT_EQUAL_INT(frozen, rig.count(MsgType::ImuRaw) + rig.count(MsgType::UsRaw));
}

static void test_bringup_ends_on_watchdog_stop() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  rig.send(makeBringup(kBringupImuRaw, 100));
  rig.step();
  TEST_ASSERT_TRUE(rig.app->bringupActive());
  rig.keepalive = false;
  rig.run(400);
  TEST_ASSERT_FALSE(rig.app->bringupActive());
}

static void test_servo_jog_steps_bounds_and_event() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  const uint8_t big = rig.send(makeBringup(kBringupServoJog, 51));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBadArgument, rig.ackFor(MsgType::Bringup, big));
  const uint8_t s = rig.send(makeBringup(kBringupServoJog, -25));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Bringup, s));
  int16_t pulse = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventServoPulse, &pulse));
  TEST_ASSERT_EQUAL_INT16(1475, pulse);  // centre 1500 - 25
  TEST_ASSERT_EQUAL_UINT16(1475, rig.robot.servo.pulse_us);
  // the SERVO_* limit parameters (800..2200 here) are ignored; the hard range 500..2500 is not
  for (int i = 0; i < 60; ++i) {
    rig.send(makeBringup(kBringupServoJog, 50));
    rig.step();
  }
  TEST_ASSERT_EQUAL_UINT16(2500, rig.robot.servo.pulse_us);
  for (int i = 0; i < 100; ++i) {
    rig.send(makeBringup(kBringupServoJog, -50));
    rig.step();
  }
  TEST_ASSERT_EQUAL_UINT16(500, rig.robot.servo.pulse_us);
  // OFF ends the jog; the pulse holds (no jump) until the next SERVO command
  rig.send(makeBringup(kBringupOff, 0));
  rig.run(100);
  TEST_ASSERT_FALSE(rig.app->bringupActive());
  TEST_ASSERT_EQUAL_UINT16(500, rig.robot.servo.pulse_us);
}

static void test_bringup_motor_duty_still_bounded_by_test_max_duty() {
  AppConfig c = testConfig();
  c.test_max_duty = 0.1f;
  Rig rig(c);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeTest));
  rig.send(makeBringup(kBringupImuRaw, 100));
  rig.drive(1000, 1000, 3000);
  TEST_ASSERT_TRUE(rig.robot.motors.left <= 0.1f + 1e-6f);
  TEST_ASSERT_TRUE(rig.app->bringupActive());
}

// ================================ battery hard cut-off ========================================
static void test_battery_cutoff_goes_safe_once_and_blocks_modes() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.paramSet(params::BATTERY_CUTOFF_MV, 9500.0f));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.drive(200, 200, 500);
  rig.robot.power.mv = 9000;
  rig.drive(200, 200, 1500);  // below for 1.5 s < hold 2 s: still driving
  TEST_ASSERT_EQUAL_UINT8(kModeDrive, rig.app->safety().mode());
  rig.drive(200, 200, 1000);
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, rig.app->safety().mode());
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  int16_t mv = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventBatteryCutoff, &mv));
  TEST_ASSERT_EQUAL_INT16(9000, mv);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.mode(kModeDrive));
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.mode(kModeTest));
  rig.run(1000);
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventBatteryCutoff));
  // charged again (above cutoff + 300 mV): modes accepted again
  rig.robot.power.mv = 12000;
  rig.run(300);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
}

static void test_battery_cutoff_zero_is_off() {
  Rig rig;
  rig.robot.power.mv = 5000;
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeDrive));
  rig.run(3000);
  TEST_ASSERT_EQUAL_INT(0, rig.events(kEventBatteryCutoff));
}

// ================================ CI pin build (S1.3) =========================================
static void test_permanent_safe_never_drives() {
  AppConfig c = testConfig();
  c.permanent_safe = true;
  c.static_diag_flags = kDiagCiPinsDoNotFlash;
  Rig rig(c);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.mode(kModeDrive));
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.mode(kModeTest));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.mode(kModeSafe));
  const uint8_t sv = rig.send(makeServo(1000));
  const uint8_t bu = rig.send(makeBringup(kBringupServoJog, 10));
  rig.drive(200, 200, 500);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Servo, sv));
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Bringup, bu));
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_INT(0, rig.robot.servo.writes);
  TEST_ASSERT_TRUE(rig.rx[rig.find(MsgType::DiagReport)].diag_report.diag_flags & kDiagCiPinsDoNotFlash);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_default_hash_equals_python);
  RUN_TEST(test_shipped_config_seeds_exactly_the_table_defaults);
  RUN_TEST(test_bounds_types_and_unknown_ids);
  RUN_TEST(test_save_reboot_reload_and_bad_nvs_values);
  RUN_TEST(test_param_set_ok_reports_and_applies);
  RUN_TEST(test_param_set_rejections_leave_value_unchanged);
  RUN_TEST(test_param_set_busy_while_moving);
  RUN_TEST(test_param_get_all_and_one);
  RUN_TEST(test_param_save_rules_and_reload_after_reboot);
  RUN_TEST(test_uncalibrating_metres_per_tick_in_drive_goes_safe);
  RUN_TEST(test_motor_invert_is_applied_at_the_output);
  RUN_TEST(test_vcomp_full_and_half_pack);
  RUN_TEST(test_vcomp_ina219_missing_uses_full_pack_constant);
  RUN_TEST(test_vcomp_voltage_jumps_never_exceed_the_cap);
  RUN_TEST(test_vcomp_caps_the_motor_duty_in_both_modes);
  RUN_TEST(test_diag_at_boot_after_boot_event_and_on_request);
  RUN_TEST(test_diag_does_not_wait_for_ever_for_the_scan);
  RUN_TEST(test_ultrasonic_presence_bits_in_diag);
  RUN_TEST(test_pcnt_wrap_at_both_ends);
  RUN_TEST(test_i2c_recovery_is_reported_once);
  RUN_TEST(test_soft_task_watchdog_model);
  RUN_TEST(test_slip_detector);
  RUN_TEST(test_slip_event_from_the_app);
  RUN_TEST(test_bringup_refused_outside_test_mode);
  RUN_TEST(test_bringup_imu_and_us_streams);
  RUN_TEST(test_bringup_ends_on_watchdog_stop);
  RUN_TEST(test_servo_jog_steps_bounds_and_event);
  RUN_TEST(test_bringup_motor_duty_still_bounded_by_test_max_duty);
  RUN_TEST(test_battery_cutoff_goes_safe_once_and_blocks_modes);
  RUN_TEST(test_battery_cutoff_zero_is_off);
  RUN_TEST(test_permanent_safe_never_drives);
  return UNITY_END();
}
