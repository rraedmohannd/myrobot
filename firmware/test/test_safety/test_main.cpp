// Safety rules of docs/SERIAL_PROTOCOL.md section 5: watchdog, front obstacle stop, rear rules,
// reverse without rear sensing, power-on SAFE mode.
#include <unity.h>

#include "scr_safety.h"

using namespace scr;
using namespace scr::proto;

void setUp() {}
void tearDown() {}

static const uint16_t kClear[4] = {2000, 2000, 2000, 2000};
static const uint16_t kNoEchoAll[4] = {kUsNoEcho, kUsNoEcho, kUsNoEcho, kUsNoEcho};

static Safety makeDriving(uint32_t now = 0) {
  Safety s{SafetyConfig{}};
  s.begin(now);
  s.onValidFrame(now);
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onMode(kModeDrive, true, true));
  s.takeHardStop();
  return s;
}

static int drainEvents(Safety& s, uint8_t code, int16_t* arg = nullptr) {
  int n = 0;
  SafetyEvent e{};
  while (s.popEvent(&e)) {
    if (e.code == code) {
      ++n;
      if (arg) *arg = e.arg;
    }
  }
  return n;
}

// ---- rule 4: power-on SAFE -------------------------------------------------------------------
static void test_power_on_is_safe_and_drive_is_rejected() {
  Safety s{SafetyConfig{}};
  s.begin(0);
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, s.mode());
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedMode, s.onDrive(200, 200, kClear));
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
}

static void test_mode_changes() {
  Safety s{SafetyConfig{}};
  s.begin(0);
  TEST_ASSERT_EQUAL_UINT8(kAckBadArgument, s.onMode(3, true, true));
  TEST_ASSERT_EQUAL_UINT8(kAckBadArgument, s.onMode(255, true, true));
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, s.mode());
  // no usable motor driver (e.g. pins not assigned): never leaves SAFE
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onMode(kModeDrive, false, true));
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onMode(kModeTest, false, true));
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, s.mode());
  // closed loop not ready (metres_per_tick not calibrated): DRIVE refused, TEST allowed
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedMode, s.onMode(kModeDrive, true, false));
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onMode(kModeTest, true, false));
  TEST_ASSERT_EQUAL_UINT8(kModeTest, s.mode());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onMode(kModeDrive, true, true));
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(100, 100, kClear));
  // a mode change always starts from standstill
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onMode(kModeSafe, true, true));
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_TRUE(s.takeHardStop());
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedMode, s.onDrive(100, 100, kClear));
}

// ---- rule 1: watchdog ------------------------------------------------------------------------
static void test_watchdog_stops_after_300_ms_of_silence() {
  Safety s = makeDriving(0);
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, kClear));
  for (uint32_t t = 10; t < 300; t += 10) {
    s.tick(t, kClear);
    TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
    TEST_ASSERT_FALSE(s.watchdogStop());
  }
  s.tick(300, kClear);
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
  TEST_ASSERT_TRUE(s.watchdogStop());
  TEST_ASSERT_TRUE(s.takeHardStop());  // no ramp-down
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventWatchdogStop, &arg));
  TEST_ASSERT_EQUAL_INT16(300, arg);
  // reported once, not every tick
  for (uint32_t t = 310; t < 1000; t += 10) s.tick(t, kClear);
  TEST_ASSERT_EQUAL_INT(0, drainEvents(s, kEventWatchdogStop));
}

static void test_any_valid_frame_feeds_the_watchdog() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  for (uint32_t t = 10; t <= 2000; t += 10) {
    if (t % 100 == 0) s.onValidFrame(t);  // heartbeats at 10 Hz
    s.tick(t, kClear);
  }
  TEST_ASSERT_FALSE(s.watchdogStop());
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
}

static void test_link_restored_but_no_motion_until_new_drive() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  for (uint32_t t = 10; t <= 500; t += 10) s.tick(t, kClear);
  TEST_ASSERT_TRUE(s.watchdogStop());
  drainEvents(s, 0);
  s.onValidFrame(510);  // e.g. a HEARTBEAT
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventLinkRestored));
  s.tick(520, kClear);
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());  // still stopped
  TEST_ASSERT_TRUE(s.watchdogStop());          // flag stays until an accepted DRIVE
  s.onValidFrame(530);
  TEST_ASSERT_EQUAL_INT(0, drainEvents(s, kEventLinkRestored));  // only once
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(150, 150, kClear));
  TEST_ASSERT_FALSE(s.watchdogStop());
  TEST_ASSERT_EQUAL_INT16(150, s.targetLeft());
}

static void test_watchdog_survives_millis_wraparound() {
  const uint32_t start = 0xFFFFFF00u;
  Safety s = makeDriving(start);
  s.onDrive(200, 200, kClear);
  s.tick(start + 200, kClear);  // wraps past zero
  TEST_ASSERT_FALSE(s.watchdogStop());
  s.tick(start + 300, kClear);
  TEST_ASSERT_TRUE(s.watchdogStop());
}

static void test_watchdog_fires_after_boot_without_any_frame() {
  Safety s{SafetyConfig{}};
  s.begin(1000);
  s.tick(1290, kClear);
  TEST_ASSERT_FALSE(s.watchdogStop());
  s.tick(1300, kClear);
  TEST_ASSERT_TRUE(s.watchdogStop());
  TEST_ASSERT_EQUAL_UINT8(kModeSafe, s.mode());
}

// ---- rule 2: front obstacle ------------------------------------------------------------------
static void test_front_obstacle_stops_forward_motion() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  s.tick(10, kClear);
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
  const uint16_t near_centre[4] = {2000, 180, kUsNoEcho, 2000};
  s.tick(20, near_centre);
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
  TEST_ASSERT_TRUE(s.obstacleStop());
  TEST_ASSERT_TRUE(s.takeHardStop());
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop, &arg));
  TEST_ASSERT_EQUAL_INT16(180, arg);
}

static void test_each_front_sensor_can_stop() {
  for (int i = 0; i < 3; ++i) {
    Safety s = makeDriving(0);
    s.onDrive(100, 300, kClear);  // forward arc
    uint16_t us[4] = {2000, 2000, 2000, 2000};
    us[i] = 249;
    s.tick(10, us);
    TEST_ASSERT_TRUE(s.obstacleStop());
    TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
  }
}

static void test_threshold_boundary() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  const uint16_t at[4] = {250, 250, 250, 2000};  // exactly the threshold: not "under"
  s.tick(10, at);
  TEST_ASSERT_FALSE(s.obstacleStop());
  const uint16_t under[4] = {249, 250, 250, 2000};
  s.tick(20, under);
  TEST_ASSERT_TRUE(s.obstacleStop());
}

static void test_forward_drive_rejected_while_blocked_whatever_the_pi_says() {
  Safety s = makeDriving(0);
  const uint16_t blocked[4] = {100, 2000, 2000, 2000};
  for (int i = 0; i < 20; ++i) {
    TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(300, 300, blocked));
    TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
    s.tick(10 + i * 10, blocked);
  }
  TEST_ASSERT_TRUE(s.obstacleStop());
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop));  // one event per blockage
}

static void test_turning_and_reversing_allowed_while_front_blocked() {
  Safety s = makeDriving(0);
  const uint16_t blocked[4] = {100, 100, 100, 2000};
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-150, 150, blocked));  // turn in place: sum == 0
  s.tick(10, blocked);
  TEST_ASSERT_EQUAL_INT16(-150, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(150, s.targetRight());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-100, -100, blocked));  // reverse, rear is clear
  s.tick(20, blocked);
  TEST_ASSERT_EQUAL_INT16(-100, s.targetLeft());
}

static void test_obstacle_flag_clears_when_front_is_free_and_forward_works_again() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  const uint16_t blocked[4] = {2000, 100, 2000, 2000};
  s.tick(10, blocked);
  TEST_ASSERT_TRUE(s.obstacleStop());
  s.tick(20, kClear);
  TEST_ASSERT_FALSE(s.obstacleStop());
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());  // does not resume by itself
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, kClear));
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
}

static void test_no_echo_is_clear_and_absent_is_ignored_unless_all_absent() {
  Safety s = makeDriving(0);
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, kNoEchoAll));
  s.tick(10, kNoEchoAll);
  TEST_ASSERT_FALSE(s.obstacleStop());
  const uint16_t one_absent[4] = {kUsAbsent, 2000, kUsNoEcho, 2000};
  s.tick(20, one_absent);
  TEST_ASSERT_FALSE(s.obstacleStop());
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
}

// ---- rule 5 ----------------------------------------------------------------------------------
static void test_all_front_sensors_absent_counts_as_blocked() {
  Safety s = makeDriving(0);
  const uint16_t all_absent[4] = {kUsAbsent, kUsAbsent, kUsAbsent, 2000};
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(200, 200, all_absent));
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop, &arg));
  TEST_ASSERT_EQUAL_INT16(-1, arg);
  // sensors die while driving forward
  Safety d = makeDriving(0);
  d.onDrive(200, 200, kClear);
  d.tick(10, all_absent);
  TEST_ASSERT_TRUE(d.obstacleStop());
  TEST_ASSERT_EQUAL_INT16(0, d.targetLeft());
  // turning in place is still possible
  TEST_ASSERT_EQUAL_UINT8(kAckOk, d.onDrive(100, -100, all_absent));
}

// ---- rule 3: reversing -----------------------------------------------------------------------
static void test_reverse_rejected_without_rear_sensor() {
  Safety s = makeDriving(0);
  const uint16_t no_rear[4] = {2000, 2000, 2000, kUsAbsent};
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(-100, -100, no_rear));
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(50, -100, no_rear));  // sum < 0
  // forward and turning in place do not need the rear sensor
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(100, 100, no_rear));
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(100, -100, no_rear));
}

static void test_reverse_allowed_with_clear_rear_and_no_echo() {
  Safety s = makeDriving(0);
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-100, -100, kClear));
  s.tick(10, kClear);
  TEST_ASSERT_EQUAL_INT16(-100, s.targetLeft());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-100, -100, kNoEchoAll));
  s.tick(20, kNoEchoAll);
  TEST_ASSERT_EQUAL_INT16(-100, s.targetLeft());
  TEST_ASSERT_FALSE(s.rearObstacleStop());
}

static void test_rear_obstacle_stops_reversing() {
  Safety s = makeDriving(0);
  s.onDrive(-100, -100, kClear);
  const uint16_t rear_near[4] = {2000, 2000, 2000, 120};
  s.tick(10, rear_near);
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_TRUE(s.rearObstacleStop());
  TEST_ASSERT_FALSE(s.obstacleStop());
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventRearObstacleStop, &arg));
  TEST_ASSERT_EQUAL_INT16(120, arg);
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(-100, -100, rear_near));
  // forward is allowed: the Pi can drive away from the rear obstacle
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(100, 100, rear_near));
  TEST_ASSERT_FALSE(s.rearObstacleStop());
}

static void test_rear_sensor_lost_while_reversing_stops() {
  Safety s = makeDriving(0);
  s.onDrive(-100, -100, kClear);
  const uint16_t rear_gone[4] = {2000, 2000, 2000, kUsAbsent};
  s.tick(10, rear_gone);
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_TRUE(s.rearObstacleStop());
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventRearObstacleStop, &arg));
  TEST_ASSERT_EQUAL_INT16(-1, arg);
}

static void test_rear_obstacle_does_not_stop_forward_motion() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  const uint16_t rear_near[4] = {2000, 2000, 2000, 50};
  s.tick(10, rear_near);
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
  TEST_ASSERT_FALSE(s.rearObstacleStop());
}

// ---- hysteresis ------------------------------------------------------------------------------
static void test_front_noise_at_threshold_gives_one_event_and_no_release() {
  Safety s = makeDriving(0);  // threshold 250, hysteresis 40 -> gone only above 290
  s.onDrive(200, 200, kClear);
  const uint16_t noise[] = {249, 251, 248, 255, 262, 250, 270, 289, 290, 252, 249, 290};
  uint32_t t = 10;
  for (uint16_t d : noise) {
    const uint16_t us[4] = {2000, d, kUsNoEcho, 2000};
    s.onValidFrame(t);
    s.tick(t, us);
    TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
    TEST_ASSERT_TRUE(s.obstacleStop());
    TEST_ASSERT_TRUE(s.frontBlockageActive());
    // the Pi keeps asking for forward: rejected every time, the stop is never released
    TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(200, 200, us));
    TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
    t += 10;
  }
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop, &arg));  // exactly one
  TEST_ASSERT_EQUAL_INT16(249, arg);
  // above threshold + hysteresis: the blockage is gone
  const uint16_t gone[4] = {2000, 291, kUsNoEcho, 2000};
  s.onValidFrame(t);
  s.tick(t, gone);
  TEST_ASSERT_FALSE(s.obstacleStop());
  TEST_ASSERT_FALSE(s.frontBlockageActive());
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());  // no motion without a new DRIVE
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, gone));
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
  // a new blockage gives a new event
  const uint16_t again[4] = {2000, 249, kUsNoEcho, 2000};
  s.tick(t + 10, again);
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop));
}

static void test_rear_noise_at_threshold_gives_one_event_and_no_release() {
  Safety s = makeDriving(0);
  s.onDrive(-100, -100, kClear);
  const uint16_t noise[] = {249, 251, 248, 255, 262, 250, 270, 289, 290, 252, 249, 290};
  uint32_t t = 10;
  for (uint16_t d : noise) {
    const uint16_t us[4] = {2000, 2000, 2000, d};
    s.onValidFrame(t);
    s.tick(t, us);
    TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
    TEST_ASSERT_TRUE(s.rearObstacleStop());
    TEST_ASSERT_TRUE(s.rearBlockageActive());
    TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(-100, -100, us));
    t += 10;
  }
  int16_t arg = 0;
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventRearObstacleStop, &arg));
  TEST_ASSERT_EQUAL_INT16(249, arg);
  const uint16_t gone[4] = {2000, 2000, 2000, 291};
  s.onValidFrame(t);
  s.tick(t, gone);
  TEST_ASSERT_FALSE(s.rearObstacleStop());
  TEST_ASSERT_FALSE(s.rearBlockageActive());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-100, -100, gone));
  TEST_ASSERT_EQUAL_INT16(-100, s.targetLeft());
}

static void test_hysteresis_applies_only_after_a_stop() {
  Safety s = makeDriving(0);
  const uint16_t just_above[4] = {255, 255, 255, 255};  // above the threshold, inside the band
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, just_above));
  s.tick(10, just_above);
  TEST_ASSERT_EQUAL_INT16(200, s.targetLeft());
  TEST_ASSERT_FALSE(s.obstacleStop());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-100, -100, just_above));
  s.tick(20, just_above);
  TEST_ASSERT_EQUAL_INT16(-100, s.targetLeft());
  TEST_ASSERT_FALSE(s.rearObstacleStop());
}

static void test_turning_away_does_not_forget_the_blockage() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  const uint16_t near_front[4] = {240, 240, 240, 2000};
  s.tick(10, near_front);
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventObstacleStop));
  // an accepted turn clears the flag ...
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-150, 150, near_front));
  TEST_ASSERT_FALSE(s.obstacleStop());
  // ... but forward inside the hysteresis band is still rejected, without a second event
  const uint16_t in_band[4] = {270, 270, 270, 2000};
  s.tick(20, in_band);
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(200, 200, in_band));
  TEST_ASSERT_TRUE(s.obstacleStop());
  TEST_ASSERT_EQUAL_INT(0, drainEvents(s, kEventObstacleStop));
}

static void test_no_echo_and_sensor_loss_with_hysteresis() {
  Safety s = makeDriving(0);
  s.onDrive(200, 200, kClear);
  const uint16_t near_front[4] = {2000, 200, 2000, 2000};
  s.tick(10, near_front);
  TEST_ASSERT_TRUE(s.frontBlockageActive());
  s.tick(20, kNoEchoAll);  // nothing in range any more: gone
  TEST_ASSERT_FALSE(s.frontBlockageActive());
  // rear: sensor lost while a rear blockage is active keeps it blocked
  s.onDrive(-100, -100, kClear);
  const uint16_t rear_near[4] = {2000, 2000, 2000, 200};
  s.tick(30, rear_near);
  TEST_ASSERT_TRUE(s.rearBlockageActive());
  const uint16_t rear_gone[4] = {2000, 2000, 2000, kUsAbsent};
  s.tick(40, rear_gone);
  TEST_ASSERT_TRUE(s.rearBlockageActive());
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(-100, -100, rear_gone));
  TEST_ASSERT_EQUAL_INT(1, drainEvents(s, kEventRearObstacleStop));
}

static void test_zero_hysteresis_releases_just_above_the_threshold() {
  SafetyConfig c;
  c.clear_hysteresis_mm = 0;
  Safety s{c};
  s.begin(0);
  s.onValidFrame(0);
  s.onMode(kModeDrive, true, true);
  s.onDrive(200, 200, kClear);
  const uint16_t under[4] = {249, 2000, 2000, 2000};
  s.tick(10, under);
  TEST_ASSERT_TRUE(s.obstacleStop());
  const uint16_t at[4] = {250, 2000, 2000, 2000};
  s.tick(20, at);
  TEST_ASSERT_TRUE(s.obstacleStop());  // "gone" is strictly above threshold + hysteresis
  const uint16_t above[4] = {251, 2000, 2000, 2000};
  s.tick(30, above);
  TEST_ASSERT_FALSE(s.obstacleStop());
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(200, 200, above));
}

// ---- STOP ------------------------------------------------------------------------------------
static void test_stop_zeroes_targets_and_skips_the_ramp() {
  Safety s = makeDriving(0);
  s.onDrive(200, -50, kClear);
  TEST_ASSERT_FALSE(s.takeHardStop());
  s.onStop();
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
  TEST_ASSERT_TRUE(s.takeHardStop());
  TEST_ASSERT_FALSE(s.takeHardStop());
}

static void test_rejected_drive_does_not_leave_an_old_drive_running() {
  Safety s = makeDriving(0);
  const uint16_t blocked[4] = {100, 100, 100, 2000};
  TEST_ASSERT_EQUAL_UINT8(kAckOk, s.onDrive(-150, 150, blocked));  // turning
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(200, 200, blocked));
  TEST_ASSERT_EQUAL_INT16(0, s.targetLeft());
  TEST_ASSERT_EQUAL_INT16(0, s.targetRight());
}

static void test_extreme_drive_values_do_not_overflow_the_sum() {
  Safety s = makeDriving(0);
  const uint16_t blocked[4] = {100, 100, 100, kUsAbsent};
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(32767, 32767, blocked));
  TEST_ASSERT_EQUAL_UINT8(kAckRejectedSafety, s.onDrive(-32768, -32768, blocked));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_power_on_is_safe_and_drive_is_rejected);
  RUN_TEST(test_mode_changes);
  RUN_TEST(test_watchdog_stops_after_300_ms_of_silence);
  RUN_TEST(test_any_valid_frame_feeds_the_watchdog);
  RUN_TEST(test_link_restored_but_no_motion_until_new_drive);
  RUN_TEST(test_watchdog_survives_millis_wraparound);
  RUN_TEST(test_watchdog_fires_after_boot_without_any_frame);
  RUN_TEST(test_front_obstacle_stops_forward_motion);
  RUN_TEST(test_each_front_sensor_can_stop);
  RUN_TEST(test_threshold_boundary);
  RUN_TEST(test_forward_drive_rejected_while_blocked_whatever_the_pi_says);
  RUN_TEST(test_turning_and_reversing_allowed_while_front_blocked);
  RUN_TEST(test_obstacle_flag_clears_when_front_is_free_and_forward_works_again);
  RUN_TEST(test_no_echo_is_clear_and_absent_is_ignored_unless_all_absent);
  RUN_TEST(test_all_front_sensors_absent_counts_as_blocked);
  RUN_TEST(test_reverse_rejected_without_rear_sensor);
  RUN_TEST(test_reverse_allowed_with_clear_rear_and_no_echo);
  RUN_TEST(test_rear_obstacle_stops_reversing);
  RUN_TEST(test_rear_sensor_lost_while_reversing_stops);
  RUN_TEST(test_rear_obstacle_does_not_stop_forward_motion);
  RUN_TEST(test_front_noise_at_threshold_gives_one_event_and_no_release);
  RUN_TEST(test_rear_noise_at_threshold_gives_one_event_and_no_release);
  RUN_TEST(test_hysteresis_applies_only_after_a_stop);
  RUN_TEST(test_turning_away_does_not_forget_the_blockage);
  RUN_TEST(test_no_echo_and_sensor_loss_with_hysteresis);
  RUN_TEST(test_zero_hysteresis_releases_just_above_the_threshold);
  RUN_TEST(test_stop_zeroes_targets_and_skips_the_ramp);
  RUN_TEST(test_rejected_drive_does_not_leave_an_old_drive_running);
  RUN_TEST(test_extreme_drive_values_do_not_overflow_the_sum);
  return UNITY_END();
}
