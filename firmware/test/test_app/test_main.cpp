// The whole application against the STUB drivers: commands in, frames out, motor duty observed.
// The "robot" here is a crude simulated plant (scr::stub::StubRobot). Nothing in this file is a
// physical result; the configuration values are test values, not calibration data.
#include <unity.h>

#include <vector>

#include "config.h"  // also proves that the placeholder configuration compiles off-target
#include "scr_app.h"
#include "scr_stub_drivers.h"

using namespace scr;
using namespace scr::proto;

void setUp() {}
void tearDown() {}

static AppConfig testConfig() {
  AppConfig c;
  c.pid.kp = 1.0f;
  c.pid.ki = 5.0f;
  c.pid.kff = 2.5f;              // plant below: 0.4 m/s at full duty
  c.metres_per_tick = 0.0001f;   // TEST VALUE
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
  FirmwareApp app;
  uint32_t now = 0;
  uint8_t seq = 0;
  bool keepalive = true;   // HEARTBEAT at 10 Hz, like the Pi
  int freeze_encoder = -1; // simulate a dead encoder
  FrameDecoder dec;
  std::vector<Event> events;
  std::vector<Ack> acks;
  Telemetry tel{};
  int tel_count = 0;
  uint32_t rx_bad = 0;

  explicit Rig(const AppConfig& c = testConfig(), bool motors_begin_ok = true)
      : app(c, robot.hardware()) {
    robot.motors.begin_result = motors_begin_ok;
    app.begin(now);
    run(300);  // every ultrasonic sensor has measured once
  }
  Rig(const Rig&) = delete;

  uint8_t send(const Message& m) {
    uint8_t buf[kMaxFrame];
    const size_t n = encodeFrame(m, seq, buf, sizeof(buf));
    robot.link.inject(buf, n);
    return seq++;
  }

  void drainTx() {
    for (uint8_t b : robot.link.tx) {
      const FrameDecoder::Feed r = dec.feed(b);
      if (r == FrameDecoder::Feed::Frame) {
        const Message& m = dec.message();
        if (m.type == MsgType::Event) events.push_back(m.event);
        if (m.type == MsgType::Ack) acks.push_back(m.ack);
        if (m.type == MsgType::Telemetry) {
          tel = m.telemetry;
          ++tel_count;
        }
      } else if (r == FrameDecoder::Feed::Bad) {
        ++rx_bad;
      }
    }
    robot.link.tx.clear();
  }

  void step() {
    now += 10;
    if (keepalive && now % 100 == 0) send(makeHeartbeat());
    robot.step(0.01f);
    if (freeze_encoder >= 0) robot.encoders.counts[freeze_encoder] = 0;
    app.controlTick(now);
    if (now % 20 == 0) app.telemetryTick(now);
    drainTx();
  }
  void run(uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) step();
  }

  int countEvents(uint8_t code, int16_t* arg = nullptr) const {
    int n = 0;
    for (const Event& e : events) {
      if (e.code == code) {
        ++n;
        if (arg) *arg = e.arg;
      }
    }
    return n;
  }
  // Status of the ACK for (type, seq); -1 when there is none.
  int ackFor(MsgType type, uint8_t s) const {
    for (const Ack& a : acks) {
      if (a.acked_type == static_cast<uint8_t>(type) && a.acked_seq == s) return a.status;
    }
    return -1;
  }
  void modeDrive() {
    const uint8_t s = send(makeMode(kModeDrive));
    step();
    TEST_ASSERT_EQUAL_INT(kAckOk, ackFor(MsgType::Mode, s));
  }
  // DRIVE at 20 Hz for `ms`.
  void drive(int16_t l, int16_t r, uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) {
      if (t % 50 == 0) send(makeDrive(l, r));
      step();
    }
  }
};

// ---- power-on --------------------------------------------------------------------------------
static void test_boot_is_safe_and_reports() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventBoot));
  TEST_ASSERT_TRUE(rig.robot.motors.begun);
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, rig.app.safety().mode());
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagMotorsEnabled);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagSimulated);  // the firmware never claims to be simulated
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagImuOk);
  TEST_ASSERT_EQUAL_UINT32(0, rig.rx_bad);
}

static void test_drive_in_safe_mode_is_rejected_and_nothing_moves() {
  Rig rig;
  const uint8_t s = rig.send(makeDrive(200, 200));
  rig.run(200);
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Drive, s));
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_INT32(0, rig.robot.encoders.counts[0]);
}

static void test_telemetry_is_50_hz_with_raw_values() {
  Rig rig;
  rig.robot.imu.sample.gyro_z_urad_s = -12345;
  rig.robot.imu.sample.accel_mg[0] = 12;
  rig.robot.imu.sample.accel_mg[1] = -34;
  rig.robot.imu.sample.accel_mg[2] = 1001;
  rig.robot.power.mv = 11850;
  rig.robot.power.ma = 1520;
  rig.robot.encoders.counts[2] = 300000;
  rig.robot.sim_pos[2] = 300000.0f;
  rig.robot.ultrasonic.setDistanceMm(1, 1200.0f);
  const int before = rig.tel_count;
  rig.run(1000);
  TEST_ASSERT_EQUAL_INT(50, rig.tel_count - before);
  TEST_ASSERT_EQUAL_UINT32(rig.now, rig.tel.t_ms);
  TEST_ASSERT_EQUAL_INT32(-12345, rig.tel.gyro_z_urad_s);
  TEST_ASSERT_EQUAL_INT16(12, rig.tel.accel_mg[0]);
  TEST_ASSERT_EQUAL_INT16(-34, rig.tel.accel_mg[1]);
  TEST_ASSERT_EQUAL_INT16(1001, rig.tel.accel_mg[2]);
  TEST_ASSERT_EQUAL_UINT16(11850, rig.tel.battery_mv);
  TEST_ASSERT_EQUAL_INT16(1520, rig.tel.battery_ma);
  TEST_ASSERT_EQUAL_INT32(300000, rig.tel.enc[2]);
  TEST_ASSERT_UINT16_WITHIN(1, 1200, rig.tel.us_mm[1]);
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, rig.tel.us_mm[0]);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagPowerSensorOk);
}

// ---- driving ---------------------------------------------------------------------------------
static void test_closed_loop_drive_ramps_and_reaches_the_target() {
  Rig rig;
  rig.modeDrive();
  TEST_ASSERT_TRUE(rig.robot.motors.enabled);
  rig.drive(200, 200, 100);
  // 500 mm/s^2 for 0.1 s -> about 50 mm/s target so far: duty far below the final 0.5
  TEST_ASSERT_TRUE(rig.app.dutyLeft() > 0.0f);
  TEST_ASSERT_TRUE(rig.app.dutyLeft() < 0.3f);
  rig.drive(200, 200, 2000);
  TEST_ASSERT_FLOAT_WITHIN(0.05f, 0.5f, rig.app.dutyLeft());
  TEST_ASSERT_FLOAT_WITHIN(0.05f, 0.5f, rig.app.dutyRight());
  TEST_ASSERT_TRUE(rig.tel.enc[0] > 1000);
  TEST_ASSERT_TRUE(rig.tel.enc[3] > 1000);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagMotorsEnabled);
  TEST_ASSERT_EQUAL_INT(0, static_cast<int>(rig.acks.size()) - 1);  // only the MODE ack: DRIVE is silent
}

static void test_drive_targets_are_clamped_to_max_speed() {
  Rig rig;
  rig.modeDrive();
  rig.drive(5000, 5000, 3000);
  // max 300 mm/s -> 0.3 m/s -> duty 0.75 on the test plant
  TEST_ASSERT_FLOAT_WITHIN(0.06f, 0.75f, rig.app.dutyLeft());
}

static void test_stop_is_immediate_and_acknowledged() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1500);
  TEST_ASSERT_TRUE(rig.app.dutyLeft() > 0.3f);
  const uint8_t s = rig.send(makeStop());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Stop, s));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);   // no ramp-down
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
  rig.run(200);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
}

static void test_test_mode_is_open_loop_and_capped() {
  Rig rig;
  const uint8_t s = rig.send(makeMode(kModeTest));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Mode, s));
  rig.drive(100, -100, 1000);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.1f, rig.robot.motors.left);  // 100 / 1000 full scale
  TEST_ASSERT_FLOAT_WITHIN(0.001f, -0.1f, rig.robot.motors.right);
  rig.drive(300, 300, 1500);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.25f, rig.robot.motors.left);  // capped at test_max_duty
}

static void test_invalid_mode_is_bad_argument() {
  Rig rig;
  const uint8_t s = rig.send(makeMode(7));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBadArgument, rig.ackFor(MsgType::Mode, s));
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, rig.app.safety().mode());
}

static void test_mode_safe_disables_the_drivers() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1000);
  rig.send(makeMode(kModeSafe));
  rig.step();
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  rig.step();
  rig.step();
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagMotorsEnabled);
}

// ---- watchdog --------------------------------------------------------------------------------
static void test_watchdog_stops_the_motors_when_the_pi_goes_silent() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1500);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.3f);
  rig.keepalive = false;  // the Pi dies
  const uint32_t silent_from = rig.now;
  uint32_t stopped_at = 0;
  for (int i = 0; i < 60; ++i) {
    rig.step();
    if (stopped_at == 0 && rig.robot.motors.left == 0.0f) stopped_at = rig.now;
  }
  TEST_ASSERT_TRUE(stopped_at != 0);
  TEST_ASSERT_TRUE(stopped_at - silent_from <= 350);  // last frame was up to 50 ms before
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventWatchdogStop));
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagWatchdogStop);

  // Link back: LINK_RESTORED, but no motion until a new DRIVE.
  rig.keepalive = true;
  rig.run(300);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventLinkRestored));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagWatchdogStop);
  rig.drive(200, 200, 500);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.0f);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagWatchdogStop);
}

static void test_garbage_does_not_feed_the_watchdog() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1000);
  rig.keepalive = false;
  for (int i = 0; i < 60; ++i) {
    // noise on the line, including frames with a wrong CRC, at 100 Hz
    uint8_t buf[kMaxFrame];
    size_t n = encodeFrame(makeDrive(200, 200), 0, buf, sizeof(buf));
    buf[3] ^= 0x10;
    rig.robot.link.inject(buf, n);
    rig.step();
  }
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventWatchdogStop));
  TEST_ASSERT_TRUE(rig.app.protocolErrors() >= 60);
  int16_t arg = 0;
  TEST_ASSERT_TRUE(rig.countEvents(kEventProtocolError, &arg) >= 1);
  TEST_ASSERT_TRUE(arg > 0);
}

static void test_unknown_type_is_acked_counted_and_does_not_feed_the_watchdog() {
  Rig rig;
  rig.keepalive = false;
  rig.run(400);  // watchdog has fired (SAFE mode, nothing moving)
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventWatchdogStop));
  // a correct frame of a type that does not exist
  uint8_t payload[4] = {0x7E, 99, 0, 0};
  const uint16_t crc = crc16Ccitt(payload, 2);
  payload[2] = static_cast<uint8_t>(crc & 0xFF);
  payload[3] = static_cast<uint8_t>(crc >> 8);
  uint8_t enc[8];
  size_t n = cobsEncode(payload, 4, enc, sizeof(enc));
  enc[n++] = 0;
  rig.robot.link.inject(enc, n);
  // and an ESP32 -> Pi message sent the wrong way
  const uint8_t s = rig.send(makeAck(1, 2, 0));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckUnknownType, rig.ackFor(static_cast<MsgType>(0x7E), 99));
  TEST_ASSERT_EQUAL_INT(kAckUnknownType, rig.ackFor(MsgType::Ack, s));
  TEST_ASSERT_EQUAL_INT(0, rig.countEvents(kEventLinkRestored));  // neither fed the watchdog
  TEST_ASSERT_EQUAL_UINT32(2, rig.app.protocolErrors());
}

// ---- obstacle stop ---------------------------------------------------------------------------
static void test_front_obstacle_stops_locally_and_rejects_forward() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1500);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.3f);
  rig.robot.ultrasonic.setDistanceMm(1, 180.0f);  // something appears in front
  rig.acks.clear();
  rig.drive(200, 200, 400);  // the Pi keeps asking for forward
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventObstacleStop, &arg));
  TEST_ASSERT_INT16_WITHIN(2, 180, arg);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagObstacleStop);
  TEST_ASSERT_TRUE(rig.acks.size() >= 1);
  for (const Ack& a : rig.acks) {
    TEST_ASSERT_EQUAL_UINT8(static_cast<uint8_t>(MsgType::Drive), a.acked_type);
    TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, a.status);
  }
  // turning in place is allowed, so the Pi can turn away
  rig.acks.clear();
  rig.drive(-150, 150, 500);
  TEST_ASSERT_EQUAL_INT(0, static_cast<int>(rig.acks.size()));
  TEST_ASSERT_TRUE(rig.robot.motors.right > 0.0f);
  TEST_ASSERT_TRUE(rig.robot.motors.left < 0.0f);
  // obstacle gone: forward works again
  rig.robot.ultrasonic.setNoEcho(1);
  rig.run(200);
  rig.drive(200, 200, 500);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.0f);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagObstacleStop);
}

static void test_noisy_distance_at_the_threshold_stops_once() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 1000);
  // noisy readings around 250 mm while the Pi keeps asking for forward
  const float noise[] = {245.0f, 256.0f, 249.0f, 262.0f, 251.0f, 275.0f, 247.0f, 285.0f};
  for (int i = 0; i < 40; ++i) {
    rig.robot.ultrasonic.setDistanceMm(1, noise[i % 8]);
    rig.drive(200, 200, 120);  // one ultrasonic round
    if (i >= 1) TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  }
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventObstacleStop));
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagObstacleStop);
  rig.robot.ultrasonic.setDistanceMm(1, 300.0f);  // above threshold + hysteresis
  rig.run(200);
  rig.drive(200, 200, 500);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.0f);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagObstacleStop);
}

static void test_reverse_needs_rear_sensing() {
  Rig rig;
  rig.modeDrive();
  rig.robot.ultrasonic.setDead(3);  // rear sensor stops answering
  rig.run(600);
  TEST_ASSERT_EQUAL_HEX16(0xFFFE, rig.tel.us_mm[3]);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagUltrasonicFault);
  const uint8_t s = rig.send(makeDrive(-100, -100));
  rig.run(100);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Drive, s));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
}

static void test_rear_obstacle_stops_reversing() {
  Rig rig;
  rig.modeDrive();
  rig.drive(-150, -150, 1000);
  TEST_ASSERT_TRUE(rig.robot.motors.left < 0.0f);
  rig.robot.ultrasonic.setDistanceMm(3, 100.0f);
  rig.drive(-150, -150, 400);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventRearObstacleStop));
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagRearObstacleStop);
}

static void test_all_front_sensors_missing_blocks_forward() {
  Rig rig;
  rig.modeDrive();
  for (uint8_t i = 0; i < 3; ++i) rig.robot.ultrasonic.setDead(i);
  rig.run(600);
  const uint8_t s = rig.send(makeDrive(200, 200));
  rig.run(100);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Drive, s));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
}

// ---- rule 8 at run time ----------------------------------------------------------------------
static void test_unusable_motor_driver_is_never_enabled() {
  Rig rig(testConfig(), /*motors_begin_ok=*/false);  // e.g. a motor pin is still -1
  const uint8_t s1 = rig.send(makeMode(kModeDrive));
  const uint8_t s2 = rig.send(makeMode(kModeTest));
  rig.run(100);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Mode, s1));
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Mode, s2));
  rig.drive(200, 200, 500);
  TEST_ASSERT_EQUAL_INT(0, rig.robot.motors.enable_calls);  // the driver was never touched
  TEST_ASSERT_FALSE(rig.robot.motors.enabled);
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagMotorsEnabled);
}

static void test_placeholder_config_cannot_move_the_robot() {
  Rig rig(cfg::makeAppConfig());  // include/config.h exactly as shipped
  const uint8_t s = rig.send(makeMode(kModeDrive));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckRejectedMode, rig.ackFor(MsgType::Mode, s));  // metres_per_tick not calibrated
  const uint8_t t = rig.send(makeMode(kModeTest));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::Mode, t));
  rig.drive(300, 300, 1000);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
  rig.send(makeServo(3000));
  rig.run(500);
  TEST_ASSERT_TRUE(rig.robot.servo.writes == 0 || rig.robot.servo.pulse_us == 1500);
}

// ---- gyro calibration ------------------------------------------------------------------------
static void test_gyro_calibration_while_still() {
  Rig rig;
  rig.robot.imu.sample.gyro_z_urad_s = 5000;  // bias
  const uint8_t s = rig.send(makeCalibrateGyro());
  rig.run(100);
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::CalibrateGyro, s));
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagGyroCalibrating);
  rig.run(2100);
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventGyroCalDone, &arg));
  TEST_ASSERT_EQUAL_INT16(50, arg);  // 5000 urad/s = 5 mrad/s = 50 x 0.1 mrad/s
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagGyroCalibrating);
  TEST_ASSERT_EQUAL_INT32(0, rig.tel.gyro_z_urad_s);  // bias removed
  rig.robot.imu.sample.gyro_z_urad_s = 105000;
  rig.run(40);
  TEST_ASSERT_EQUAL_INT32(100000, rig.tel.gyro_z_urad_s);
}

static void test_gyro_calibration_busy_while_moving_and_drive_busy_while_calibrating() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 500);
  const uint8_t s = rig.send(makeCalibrateGyro());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBusy, rig.ackFor(MsgType::CalibrateGyro, s));
  rig.send(makeStop());
  rig.run(600);  // still
  const uint8_t s2 = rig.send(makeCalibrateGyro());
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::CalibrateGyro, s2));
  const uint8_t s3 = rig.send(makeCalibrateGyro());
  const uint8_t s4 = rig.send(makeDrive(200, 200));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckBusy, rig.ackFor(MsgType::CalibrateGyro, s3));
  TEST_ASSERT_EQUAL_INT(kAckBusy, rig.ackFor(MsgType::Drive, s4));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
}

static void test_gyro_calibration_fails_when_the_robot_is_pushed() {
  Rig rig;
  rig.send(makeCalibrateGyro());
  rig.run(500);
  rig.robot.sim_pos[0] += 25.0f;  // someone pushes the robot: an encoder counts
  rig.run(100);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventGyroCalFailed));
  TEST_ASSERT_EQUAL_INT(0, rig.countEvents(kEventGyroCalDone));
  TEST_ASSERT_EQUAL_INT32(0, rig.app.gyroBiasUradS());
}

static void test_imu_fault_is_reported_once_and_flag_drops() {
  Rig rig;
  rig.robot.imu.read_result = false;
  rig.run(500);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventImuFault));
  TEST_ASSERT_FALSE(rig.tel.flags & kFlagImuOk);
  TEST_ASSERT_EQUAL_INT32(0, rig.tel.gyro_z_urad_s);
}

// ---- servo, power, encoders ------------------------------------------------------------------
static void test_servo_silent_until_commanded_then_slews_and_clamps() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(0, rig.robot.servo.writes);  // no pulse at power-on
  rig.send(makeServo(3000));  // +30 deg (left)
  rig.run(100);
  TEST_ASSERT_TRUE(rig.robot.servo.pulse_us > 1500);
  TEST_ASSERT_TRUE(rig.robot.servo.pulse_us < 1700);  // 90 deg/s: about 9 deg after 0.1 s
  rig.run(500);
  TEST_ASSERT_EQUAL_UINT16(1800, rig.robot.servo.pulse_us);
  rig.send(makeServo(-20000));  // beyond the limit
  rig.run(2000);
  TEST_ASSERT_EQUAL_UINT16(900, rig.robot.servo.pulse_us);  // clamped to -60 deg
  TEST_ASSERT_EQUAL_INT(0, static_cast<int>(rig.acks.size()));  // SERVO is not acknowledged
}

static void test_low_battery_event_and_flag() {
  Rig rig;
  rig.robot.power.mv = 9800;
  rig.run(1000);
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventLowBattery, &arg));
  TEST_ASSERT_EQUAL_INT16(9800, arg);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagLowBattery);
  rig.run(2000);
  TEST_ASSERT_EQUAL_INT(1, rig.countEvents(kEventLowBattery));
}

static void test_dead_encoder_stops_closed_loop_driving() {
  Rig rig;
  rig.freeze_encoder = 1;
  rig.modeDrive();
  rig.drive(200, 200, 400);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.2f);
  rig.keepalive = true;
  rig.run(600);  // the last DRIVE target stays; the fault timer runs out
  int16_t arg = -1;
  TEST_ASSERT_TRUE(rig.countEvents(kEventEncoderFault, &arg) >= 1);
  TEST_ASSERT_EQUAL_INT16(1, arg);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_TRUE(rig.tel.flags & kFlagEncoderFault);
  // closed-loop DRIVE stays refused while the encoder is dead
  const uint8_t s = rig.send(makeDrive(200, 200));
  rig.run(300);
  TEST_ASSERT_EQUAL_INT(kAckRejectedSafety, rig.ackFor(MsgType::Drive, s));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  // the encoder counts again (repaired): DRIVE is accepted
  rig.freeze_encoder = -1;
  rig.robot.sim_pos[1] += 10.0f;
  rig.run(50);
  rig.drive(200, 200, 300);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.0f);
}

static void test_sequence_numbers_of_sent_frames_have_no_gaps() {
  Rig rig;
  rig.modeDrive();
  rig.drive(200, 200, 3000);
  TEST_ASSERT_EQUAL_UINT32(0, rig.dec.lostFrames());
  TEST_ASSERT_EQUAL_UINT32(0, rig.rx_bad);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_boot_is_safe_and_reports);
  RUN_TEST(test_drive_in_safe_mode_is_rejected_and_nothing_moves);
  RUN_TEST(test_telemetry_is_50_hz_with_raw_values);
  RUN_TEST(test_closed_loop_drive_ramps_and_reaches_the_target);
  RUN_TEST(test_drive_targets_are_clamped_to_max_speed);
  RUN_TEST(test_stop_is_immediate_and_acknowledged);
  RUN_TEST(test_test_mode_is_open_loop_and_capped);
  RUN_TEST(test_invalid_mode_is_bad_argument);
  RUN_TEST(test_mode_safe_disables_the_drivers);
  RUN_TEST(test_watchdog_stops_the_motors_when_the_pi_goes_silent);
  RUN_TEST(test_garbage_does_not_feed_the_watchdog);
  RUN_TEST(test_unknown_type_is_acked_counted_and_does_not_feed_the_watchdog);
  RUN_TEST(test_front_obstacle_stops_locally_and_rejects_forward);
  RUN_TEST(test_noisy_distance_at_the_threshold_stops_once);
  RUN_TEST(test_reverse_needs_rear_sensing);
  RUN_TEST(test_rear_obstacle_stops_reversing);
  RUN_TEST(test_all_front_sensors_missing_blocks_forward);
  RUN_TEST(test_unusable_motor_driver_is_never_enabled);
  RUN_TEST(test_placeholder_config_cannot_move_the_robot);
  RUN_TEST(test_gyro_calibration_while_still);
  RUN_TEST(test_gyro_calibration_busy_while_moving_and_drive_busy_while_calibrating);
  RUN_TEST(test_gyro_calibration_fails_when_the_robot_is_pushed);
  RUN_TEST(test_imu_fault_is_reported_once_and_flag_drops);
  RUN_TEST(test_servo_silent_until_commanded_then_slews_and_clamps);
  RUN_TEST(test_low_battery_event_and_flag);
  RUN_TEST(test_dead_encoder_stops_closed_loop_driving);
  RUN_TEST(test_sequence_numbers_of_sent_frames_have_no_gaps);
  return UNITY_END();
}
