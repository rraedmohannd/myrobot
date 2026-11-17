// PID, ramping, servo slew/limits, gyro bias calibration, ultrasonic scheduling, battery
// monitor, encoder fault detector. All values in these tests are test values, not robot data.
#include <unity.h>

#include <cmath>

#include "scr_control.h"
#include "scr_stub_drivers.h"
#include "scr_ultrasonic.h"

using namespace scr;

void setUp() {}
void tearDown() {}

// ---- PID -------------------------------------------------------------------------------------
static void test_pid_zero_gains_give_zero_output() {
  Pid pid{PidGains{}};
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pid.update(0.5f, 0.0f, 0.01f));
}

static void test_pid_proportional_and_feedforward() {
  PidGains g;
  g.kp = 2.0f;
  g.kff = 0.5f;
  Pid pid{g};
  // error 0.1 -> 0.2, ff 0.2 * 0.5 = 0.1
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.3f, pid.update(0.2f, 0.1f, 0.01f));
}

static void test_pid_integral_accumulates_and_resets() {
  PidGains g;
  g.ki = 1.0f;
  Pid pid{g};
  float out = 0.0f;
  for (int i = 0; i < 100; ++i) out = pid.update(0.5f, 0.0f, 0.01f);  // 1 s of error 0.5
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 0.5f, out);
  pid.reset();
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pid.integral());
}

static void test_pid_output_clamped_and_no_windup() {
  PidGains g;
  g.kp = 1.0f;
  g.ki = 10.0f;
  Pid pid{g};
  for (int i = 0; i < 1000; ++i) {
    const float out = pid.update(10.0f, 0.0f, 0.01f);  // saturated for 10 s
    TEST_ASSERT_TRUE(out <= 1.0f && out >= -1.0f);
  }
  TEST_ASSERT_TRUE(pid.integral() <= 1.0f);  // did not wind up to 1000
  // When the error reverses, the output leaves saturation at once.
  const float out = pid.update(0.0f, 0.5f, 0.01f);
  TEST_ASSERT_TRUE(out < 1.0f);
}

static void test_pid_derivative_on_measurement() {
  PidGains g;
  g.kd = 0.1f;
  Pid pid{g};
  TEST_ASSERT_EQUAL_FLOAT(0.0f, pid.update(1.0f, 0.0f, 0.01f));  // first call: no derivative kick
  // measurement rises by 0.05 in 0.01 s -> -0.1 * 5 = -0.5
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, -0.5f, pid.update(1.0f, 0.05f, 0.01f));
}

static void test_pid_closes_the_loop_on_a_first_order_plant() {
  PidGains g;
  g.kp = 1.0f;
  g.ki = 5.0f;
  Pid pid{g};
  float speed = 0.0f;
  for (int i = 0; i < 1000; ++i) {
    const float duty = pid.update(0.3f, speed, 0.01f);
    speed += (duty * 1.0f - speed) * 0.05f;  // plant: 1 m/s at full duty, lag
  }
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 0.3f, speed);
}

// ---- ramp ------------------------------------------------------------------------------------
static void test_ramp_limits_rise_and_fall() {
  RampLimiter r(500.0f);  // 500 units/s -> 5 per 10 ms tick
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 5.0f, r.update(300.0f, 0.01f));
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 10.0f, r.update(300.0f, 0.01f));
  for (int i = 0; i < 100; ++i) r.update(300.0f, 0.01f);
  TEST_ASSERT_EQUAL_FLOAT(300.0f, r.value());  // reaches the target exactly, no overshoot
  TEST_ASSERT_FLOAT_WITHIN(1e-3f, 295.0f, r.update(-300.0f, 0.01f));
  for (int i = 0; i < 200; ++i) r.update(-300.0f, 0.01f);
  TEST_ASSERT_EQUAL_FLOAT(-300.0f, r.value());
}

static void test_ramp_reset_jumps_to_zero() {
  RampLimiter r(500.0f);
  for (int i = 0; i < 50; ++i) r.update(300.0f, 0.01f);
  TEST_ASSERT_TRUE(r.value() > 0.0f);
  r.reset(0.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.value());
}

static void test_ramp_zero_rate_never_moves() {
  RampLimiter r(0.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, r.update(300.0f, 0.01f));
}

// ---- servo -----------------------------------------------------------------------------------
static ServoConfig servoCfg() {
  ServoConfig c;
  c.min_cdeg = -6000;
  c.max_cdeg = 6000;
  c.slew_cdeg_per_s = 9000.0f;  // 90 deg/s
  c.center_pulse_us = 1500.0f;
  c.us_per_deg = 10.0f;
  c.pulse_min_us = 800;
  c.pulse_max_us = 2200;
  return c;
}

static void test_servo_clamps_to_limits() {
  ServoController s(servoCfg());
  TEST_ASSERT_FALSE(s.commanded());
  s.setTarget(20000);
  TEST_ASSERT_TRUE(s.commanded());
  TEST_ASSERT_EQUAL_INT16(6000, s.targetCdeg());
  s.setTarget(-20000);
  TEST_ASSERT_EQUAL_INT16(-6000, s.targetCdeg());
  s.setTarget(-1234);
  TEST_ASSERT_EQUAL_INT16(-1234, s.targetCdeg());
}

static void test_servo_slew_rate() {
  ServoController s(servoCfg());
  s.setTarget(4500);
  s.update(0.01f);  // 90 deg/s * 0.01 s = 0.9 deg
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 90.0f, s.currentCdeg());
  for (int i = 0; i < 24; ++i) s.update(0.01f);  // 0.25 s in total -> 22.5 deg
  TEST_ASSERT_FLOAT_WITHIN(0.5f, 2250.0f, s.currentCdeg());
  for (int i = 0; i < 100; ++i) s.update(0.01f);
  TEST_ASSERT_EQUAL_FLOAT(4500.0f, s.currentCdeg());  // arrives, no overshoot
}

static void test_servo_pulse_mapping_and_hard_limits() {
  ServoConfig c = servoCfg();
  ServoController s(c);
  TEST_ASSERT_EQUAL_UINT16(1500, s.pulseUs());
  s.setTarget(3000);
  for (int i = 0; i < 200; ++i) s.update(0.01f);
  TEST_ASSERT_EQUAL_UINT16(1800, s.pulseUs());  // +30 deg * 10 us
  c.invert = true;
  ServoController inv(c);
  inv.setTarget(3000);
  for (int i = 0; i < 200; ++i) inv.update(0.01f);
  TEST_ASSERT_EQUAL_UINT16(1200, inv.pulseUs());
  c.invert = false;
  c.pulse_max_us = 1700;  // the hard pulse limit wins over the angle
  ServoController lim(c);
  lim.setTarget(6000);
  for (int i = 0; i < 200; ++i) lim.update(0.01f);
  TEST_ASSERT_EQUAL_UINT16(1700, lim.pulseUs());
}

static void test_servo_placeholder_config_does_not_move() {
  ServoController s{ServoConfig{}};  // all-zero limits and slew
  s.setTarget(3000);
  s.update(1.0f);
  TEST_ASSERT_EQUAL_INT16(0, s.targetCdeg());
  TEST_ASSERT_EQUAL_UINT16(1500, s.pulseUs());
}

// ---- gyro bias calibration -------------------------------------------------------------------
static GyroCalConfig gyroCfg() {
  GyroCalConfig c;
  c.samples = 200;
  c.max_spread_urad_s = 20000;
  return c;
}

static void test_gyro_cal_averages_while_still() {
  GyroBiasCalibrator g(gyroCfg());
  TEST_ASSERT_TRUE(g.addSample(123, false) == GyroBiasCalibrator::State::Idle);  // not started
  g.start();
  TEST_ASSERT_TRUE(g.running());
  GyroBiasCalibrator::State st = GyroBiasCalibrator::State::Idle;
  for (int i = 0; i < 200; ++i) {
    st = g.addSample(5000 + ((i % 2) ? 300 : -300), false);  // bias 5000 with noise
    if (i < 199) TEST_ASSERT_TRUE(st == GyroBiasCalibrator::State::Running);
  }
  TEST_ASSERT_TRUE(st == GyroBiasCalibrator::State::Done);
  TEST_ASSERT_FALSE(g.running());
  TEST_ASSERT_INT32_WITHIN(2, 5000, g.biasUradS());
}

static void test_gyro_cal_fails_when_encoders_move() {
  GyroBiasCalibrator g(gyroCfg());
  g.start();
  for (int i = 0; i < 50; ++i) g.addSample(1000, false);
  TEST_ASSERT_TRUE(g.addSample(1000, true) == GyroBiasCalibrator::State::Failed);
  TEST_ASSERT_FALSE(g.running());
  TEST_ASSERT_EQUAL_INT32(0, g.biasUradS());  // old bias kept (none yet)
}

static void test_gyro_cal_fails_when_rate_spread_too_large() {
  GyroBiasCalibrator g(gyroCfg());
  g.start();
  for (int i = 0; i < 10; ++i) g.addSample(1000, false);
  TEST_ASSERT_TRUE(g.addSample(40000, false) == GyroBiasCalibrator::State::Failed);
}

static void test_gyro_cal_failure_keeps_previous_bias() {
  GyroBiasCalibrator g(gyroCfg());
  g.start();
  for (int i = 0; i < 200; ++i) g.addSample(-700, false);
  TEST_ASSERT_EQUAL_INT32(-700, g.biasUradS());
  g.start();
  g.addSample(0, false);
  g.abort();
  TEST_ASSERT_TRUE(g.addSample(0, false) == GyroBiasCalibrator::State::Failed);
  TEST_ASSERT_EQUAL_INT32(-700, g.biasUradS());
}

// ---- ultrasonic scheduling -------------------------------------------------------------------
static void runUs(UltrasonicScheduler& s, uint32_t& now, uint32_t ms) {
  for (uint32_t t = 0; t < ms; t += 10) {
    s.tick(now);
    now += 10;
  }
}

static void test_ultrasonic_starts_absent_until_measured() {
  stub::StubUltrasonic hw;
  UltrasonicScheduler s(UltrasonicConfig{}, &hw);
  s.begin(true);
  for (int i = 0; i < 4; ++i) TEST_ASSERT_EQUAL_HEX16(0xFFFE, s.distancesMm()[i]);
  TEST_ASSERT_TRUE(s.anyFault());
}

static void test_ultrasonic_staggered_one_at_a_time() {
  stub::StubUltrasonic hw;
  UltrasonicConfig c;  // slot 30 ms, order FL, FR, FC, rear
  UltrasonicScheduler s(c, &hw);
  s.begin(true);
  uint32_t now = 0;
  runUs(s, now, 480);  // 16 slots
  TEST_ASSERT_EQUAL_INT(0, hw.overlap_errors);  // never two sensors in the air
  TEST_ASSERT_TRUE(hw.trigger_log.size() >= 15);
  const uint8_t expected[] = {0, 2, 1, 3, 0, 2, 1, 3};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, hw.trigger_log.data(), 8);
}

static void test_ultrasonic_slot_spacing() {
  stub::StubUltrasonic hw;
  UltrasonicConfig c;
  c.slot_ms = 30;
  UltrasonicScheduler s(c, &hw);
  s.begin(true);
  uint32_t now = 0;
  s.tick(now);  // trigger #1 at t=0
  TEST_ASSERT_EQUAL(1, hw.trigger_log.size());
  s.tick(10);
  s.tick(20);
  TEST_ASSERT_EQUAL(1, hw.trigger_log.size());  // slot still open
  s.tick(30);
  TEST_ASSERT_EQUAL(2, hw.trigger_log.size());
}

static void test_ultrasonic_distance_no_echo_and_range() {
  stub::StubUltrasonic hw;
  hw.setDistanceMm(0, 250.0f);
  hw.setNoEcho(1);
  hw.setDistanceMm(2, 6000.0f);  // beyond max range
  hw.setDistanceMm(3, 1200.0f);
  UltrasonicScheduler s(UltrasonicConfig{}, &hw);
  s.begin(true);
  uint32_t now = 0;
  runUs(s, now, 200);
  TEST_ASSERT_UINT16_WITHIN(1, 250, s.distancesMm()[0]);
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, s.distancesMm()[1]);
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, s.distancesMm()[2]);
  TEST_ASSERT_UINT16_WITHIN(1, 1200, s.distancesMm()[3]);
  TEST_ASSERT_FALSE(s.anyFault());
}

static void test_ultrasonic_dead_sensor_becomes_absent_after_misses() {
  stub::StubUltrasonic hw;
  for (uint8_t i = 0; i < 4; ++i) hw.setDistanceMm(i, 800.0f);
  UltrasonicConfig c;
  c.fault_after_misses = 3;
  UltrasonicScheduler s(c, &hw);
  s.begin(true);
  uint32_t now = 0;
  runUs(s, now, 200);
  TEST_ASSERT_FALSE(s.anyFault());
  hw.setDead(1);
  runUs(s, now, 240);  // two rounds: 2 misses, value kept
  TEST_ASSERT_UINT16_WITHIN(1, 800, s.distancesMm()[1]);
  runUs(s, now, 240);
  TEST_ASSERT_EQUAL_HEX16(0xFFFE, s.distancesMm()[1]);
  TEST_ASSERT_TRUE(s.anyFault());
  hw.setDistanceMm(1, 500.0f);  // comes back
  runUs(s, now, 200);
  TEST_ASSERT_UINT16_WITHIN(1, 500, s.distancesMm()[1]);
}

static void test_ultrasonic_unwired_sensor_is_skipped_and_absent() {
  stub::StubUltrasonic hw;
  for (uint8_t i = 0; i < 4; ++i) hw.setDistanceMm(i, 800.0f);
  hw.is_present[3] = false;  // no rear sensor
  UltrasonicScheduler s(UltrasonicConfig{}, &hw);
  s.begin(true);
  uint32_t now = 0;
  runUs(s, now, 300);
  for (uint8_t fired : hw.trigger_log) TEST_ASSERT_NOT_EQUAL(3, fired);
  TEST_ASSERT_EQUAL_HEX16(0xFFFE, s.distancesMm()[3]);
  TEST_ASSERT_UINT16_WITHIN(1, 800, s.distancesMm()[0]);
}

static void test_ultrasonic_driver_not_started_triggers_nothing() {
  stub::StubUltrasonic hw;
  UltrasonicScheduler s(UltrasonicConfig{}, &hw);
  s.begin(false);
  uint32_t now = 0;
  runUs(s, now, 300);
  TEST_ASSERT_EQUAL(0, hw.trigger_log.size());
  for (int i = 0; i < 4; ++i) TEST_ASSERT_EQUAL_HEX16(0xFFFE, s.distancesMm()[i]);
}

// ---- battery ---------------------------------------------------------------------------------
static void test_battery_low_needs_hold_time_and_fires_once() {
  BatteryConfig c;
  c.low_mv = 10000;
  c.recover_mv = 10500;
  c.hold_ms = 2000;
  BatteryMonitor b(c);
  TEST_ASSERT_FALSE(b.update(0, 9900, true));
  TEST_ASSERT_FALSE(b.update(1000, 9900, true));
  TEST_ASSERT_FALSE(b.update(1500, 10100, true));  // a recovery restarts the timer
  TEST_ASSERT_FALSE(b.update(2000, 9900, true));
  TEST_ASSERT_FALSE(b.update(3900, 9900, true));
  TEST_ASSERT_TRUE(b.update(4000, 9900, true));
  TEST_ASSERT_TRUE(b.low());
  TEST_ASSERT_FALSE(b.update(5000, 9800, true));  // only once
  TEST_ASSERT_FALSE(b.update(6000, 10200, true));  // hysteresis: still low
  TEST_ASSERT_TRUE(b.low());
  b.update(7000, 10600, true);
  TEST_ASSERT_FALSE(b.low());
}

static void test_battery_off_when_threshold_zero_or_sensor_bad() {
  BatteryMonitor off{BatteryConfig{}};
  TEST_ASSERT_FALSE(off.update(0, 0, true));
  TEST_ASSERT_FALSE(off.update(100000, 0, true));
  BatteryConfig c;
  c.low_mv = 10000;
  c.recover_mv = 10500;
  c.hold_ms = 0;
  BatteryMonitor b(c);
  TEST_ASSERT_FALSE(b.update(0, 0, false));  // a failed read is not "0 V"
  TEST_ASSERT_FALSE(b.low());
}

// ---- encoder fault ---------------------------------------------------------------------------
static void test_encoder_fault_detected_and_rearmed() {
  EncoderFaultConfig c;
  c.min_duty = 0.3f;
  c.timeout_ms = 500;
  EncoderFaultDetector d(c);
  TEST_ASSERT_FALSE(d.update(0, 0.5f, 0));
  TEST_ASSERT_FALSE(d.update(400, 0.5f, 0));
  TEST_ASSERT_TRUE(d.update(500, -0.5f, 0));
  TEST_ASSERT_TRUE(d.faulty());
  TEST_ASSERT_FALSE(d.update(600, 0.5f, 0));  // reported once
  TEST_ASSERT_FALSE(d.update(700, 0.5f, 3));  // counts again
  TEST_ASSERT_FALSE(d.faulty());
}

static void test_encoder_fault_not_raised_at_low_duty_or_when_off() {
  EncoderFaultConfig c;
  c.min_duty = 0.3f;
  c.timeout_ms = 500;
  EncoderFaultDetector d(c);
  for (uint32_t t = 0; t < 5000; t += 100) TEST_ASSERT_FALSE(d.update(t, 0.1f, 0));
  EncoderFaultDetector off{EncoderFaultConfig{}};
  for (uint32_t t = 0; t < 5000; t += 100) TEST_ASSERT_FALSE(off.update(t, 1.0f, 0));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_pid_zero_gains_give_zero_output);
  RUN_TEST(test_pid_proportional_and_feedforward);
  RUN_TEST(test_pid_integral_accumulates_and_resets);
  RUN_TEST(test_pid_output_clamped_and_no_windup);
  RUN_TEST(test_pid_derivative_on_measurement);
  RUN_TEST(test_pid_closes_the_loop_on_a_first_order_plant);
  RUN_TEST(test_ramp_limits_rise_and_fall);
  RUN_TEST(test_ramp_reset_jumps_to_zero);
  RUN_TEST(test_ramp_zero_rate_never_moves);
  RUN_TEST(test_servo_clamps_to_limits);
  RUN_TEST(test_servo_slew_rate);
  RUN_TEST(test_servo_pulse_mapping_and_hard_limits);
  RUN_TEST(test_servo_placeholder_config_does_not_move);
  RUN_TEST(test_gyro_cal_averages_while_still);
  RUN_TEST(test_gyro_cal_fails_when_encoders_move);
  RUN_TEST(test_gyro_cal_fails_when_rate_spread_too_large);
  RUN_TEST(test_gyro_cal_failure_keeps_previous_bias);
  RUN_TEST(test_ultrasonic_starts_absent_until_measured);
  RUN_TEST(test_ultrasonic_staggered_one_at_a_time);
  RUN_TEST(test_ultrasonic_slot_spacing);
  RUN_TEST(test_ultrasonic_distance_no_echo_and_range);
  RUN_TEST(test_ultrasonic_dead_sensor_becomes_absent_after_misses);
  RUN_TEST(test_ultrasonic_unwired_sensor_is_skipped_and_absent);
  RUN_TEST(test_ultrasonic_driver_not_started_triggers_nothing);
  RUN_TEST(test_battery_low_needs_hold_time_and_fires_once);
  RUN_TEST(test_battery_off_when_threshold_zero_or_sensor_bad);
  RUN_TEST(test_encoder_fault_detected_and_rearmed);
  RUN_TEST(test_encoder_fault_not_raised_at_low_duty_or_when_off);
  return UNITY_END();
}
