// Independent safety review (docs/SPRINT_5.md S2.8): regression tests for the defects it found.
// STUB drivers only (scr::stub::StubRobot): nothing here is a physical result; the configuration
// values are test values, not calibration data.
#include <unity.h>

#include <vector>

#include "config.h"
#include "scr_app.h"
#include "scr_params.h"
#include "scr_stub_drivers.h"

using namespace scr;
using namespace scr::proto;

void setUp() {}
void tearDown() {}

// Same test values as test_app / test_sprint5 (TEST VALUES, not calibration data).
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
  c.encoder_fault.min_duty = 0.2f;
  c.encoder_fault.timeout_ms = 500;
  return c;
}

struct Rig {
  stub::StubRobot robot;
  FirmwareApp app;
  uint32_t now = 0;
  uint8_t seq = 0;
  FrameDecoder dec;
  std::vector<Message> rx;

  explicit Rig(const AppConfig& c = testConfig()) : app(c, robot.hardware()) {
    app.begin(now);
    run(300);
  }
  Rig(const Rig&) = delete;

  uint8_t send(const Message& m) {
    uint8_t buf[kMaxFrame];
    const size_t n = encodeFrame(m, seq, buf, sizeof(buf));
    robot.link.inject(buf, n);
    return seq++;
  }
  void step() {
    now += 10;
    if (now % 100 == 0) send(makeHeartbeat());
    robot.step(0.01f);
    app.controlTick(now);
    if (now % 20 == 0) app.telemetryTick(now);
    for (uint8_t b : robot.link.tx) {
      if (dec.feed(b) == FrameDecoder::Feed::Frame) rx.push_back(dec.message());
    }
    robot.link.tx.clear();
  }
  void run(uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) step();
  }
  void drive(int16_t l, int16_t r, uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 10) {
      if (t % 50 == 0) send(makeDrive(l, r));
      step();
    }
  }
  int events(uint8_t code) const {
    int n = 0;
    for (const Message& m : rx) n += (m.type == MsgType::Event && m.event.code == code) ? 1 : 0;
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
  void modeDrive() {
    const uint8_t s = send(makeMode(kModeDrive));
    step();
    TEST_ASSERT_EQUAL_INT(kAckOk, ackFor(MsgType::Mode, s));
  }
};

// ---- finding 1: the obstacle rules looked only at the TARGETS, not at the ramped motion -------
// A DRIVE whose target sum is 0 (a smooth stop "DRIVE 0 0", or a turn in place) is accepted
// while the ramp still carries the robot forward / backward for up to MAX_SPEED / RAMP seconds.
// During that ramp-down the front (rear) rule must still stop the robot.

static void test_front_obstacle_stops_the_ramp_down_after_drive_zero() {
  Rig rig;
  rig.modeDrive();
  rig.drive(300, 300, 1500);  // ramp at 300 mm/s (500 mm/s^2: a 0.6 s ramp-down)
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.3f);
  rig.robot.ultrasonic.setDistanceMm(1, 180.0f);  // something appears in front
  rig.send(makeDrive(0, 0));                      // the Pi asks for a smooth stop
  rig.run(200);  // one ultrasonic round (4 x 30 ms) plus margin
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventObstacleStop));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
}

static void test_front_obstacle_stops_a_turn_that_still_moves_forward() {
  Rig rig;
  rig.modeDrive();
  rig.drive(300, 300, 1500);
  rig.robot.ultrasonic.setDistanceMm(1, 180.0f);
  // Target sum 0 ("turn in place") is accepted, but the right track needs 0.6 s to come down
  // from +300 through 0: until then the robot still moves forward.
  rig.send(makeDrive(300, -300));
  rig.run(200);
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventObstacleStop));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
  // After the stop a real turn in place (from standstill) is still allowed.
  rig.drive(300, -300, 300);
  TEST_ASSERT_TRUE(rig.robot.motors.left > 0.0f);
  TEST_ASSERT_TRUE(rig.robot.motors.right < 0.0f);
}

static void test_rear_obstacle_stops_the_ramp_down_after_drive_zero() {
  Rig rig;
  rig.modeDrive();
  rig.drive(-300, -300, 1500);
  TEST_ASSERT_TRUE(rig.robot.motors.left < -0.3f);
  rig.robot.ultrasonic.setDistanceMm(3, 100.0f);
  rig.send(makeDrive(0, 0));
  rig.run(200);
  TEST_ASSERT_EQUAL_INT(1, rig.events(kEventRearObstacleStop));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, rig.robot.motors.right);
}

static void test_turn_in_place_from_standstill_is_not_stopped_by_a_front_obstacle() {
  // Unchanged rule 2: a symmetric turn in place is allowed with the front blocked.
  Rig rig;
  rig.modeDrive();
  rig.robot.ultrasonic.setDistanceMm(1, 180.0f);
  rig.run(200);
  rig.drive(-200, 200, 600);
  TEST_ASSERT_EQUAL_INT(0, rig.events(kEventObstacleStop));
  TEST_ASSERT_TRUE(rig.robot.motors.left < 0.0f);
  TEST_ASSERT_TRUE(rig.robot.motors.right > 0.0f);
}

// ---- finding 2: changing an ENCODER_INVERT_* parameter made a phantom encoder jump ----------
// The cumulative count flips sign, so the next delta is -2 x count: a huge phantom track speed.
// With speed filtering it lasts many ticks and the PID answers a forward DRIVE with full
// reverse duty (no rear check: the target is forward).
static void test_encoder_inversion_change_makes_no_phantom_speed() {
  AppConfig c = testConfig();
  c.speed_filter_alpha = 0.05f;  // allowed range 0.01..1
  Rig rig(c);
  rig.modeDrive();
  rig.drive(-200, -200, 1500);  // the left counts are now large and negative
  TEST_ASSERT_TRUE(rig.robot.encoders.counts[0] < -500);
  rig.send(makeStop());
  rig.run(1000);  // still; the filtered speed has decayed below the BUSY limit
  // The student fixes a left side that was wired backwards: motor AND both encoders inverted.
  const uint8_t s1 = rig.send(makeParamSet(params::MOTOR_INVERT_LEFT, 1.0f));
  const uint8_t s2 = rig.send(makeParamSet(params::ENCODER_INVERT_FL, 1.0f));
  const uint8_t s3 = rig.send(makeParamSet(params::ENCODER_INVERT_RL, 1.0f));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSet, s1));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSet, s2));
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSet, s3));
  rig.run(20);
  // No phantom motion: another parameter is not BUSY.
  const uint8_t s4 = rig.send(makeParamSet(params::SLIP_RATIO, 0.3f));
  rig.step();
  TEST_ASSERT_EQUAL_INT(kAckOk, rig.ackFor(MsgType::ParamSet, s4));
  // A forward DRIVE never produces reverse duty on the left track.
  for (uint32_t t = 0; t < 400; t += 10) {
    if (t % 50 == 0) rig.send(makeDrive(150, 150));
    rig.step();
    TEST_ASSERT_TRUE(rig.app.dutyLeft() >= 0.0f);
  }
  TEST_ASSERT_TRUE(rig.app.dutyLeft() > 0.0f);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_front_obstacle_stops_the_ramp_down_after_drive_zero);
  RUN_TEST(test_front_obstacle_stops_a_turn_that_still_moves_forward);
  RUN_TEST(test_rear_obstacle_stops_the_ramp_down_after_drive_zero);
  RUN_TEST(test_turn_in_place_from_standstill_is_not_stopped_by_a_front_obstacle);
  RUN_TEST(test_encoder_inversion_change_makes_no_phantom_speed);
  return UNITY_END();
}
