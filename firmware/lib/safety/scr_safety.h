// Local safety rules of docs/SERIAL_PROTOCOL.md section 5 (CLAUDE.md rule 4).
// They hold whatever the Pi or the backend says. Pure logic, no hardware access.
//
//  1. Watchdog: no valid frame for 300 ms -> targets zero, flag + event WATCHDOG_STOP.
//     The next valid frame gives LINK_RESTORED; motion only after a new accepted DRIVE.
//  2. Front obstacle stop: commanded motion forward (left + right > 0) and nearest front
//     distance under the threshold -> stop, flag + event OBSTACLE_STOP. Forward DRIVE is
//     rejected (REJECTED_SAFETY) while blocked. Turning in place and reversing stay allowed.
//  3. Reversing (left + right < 0) needs rear sensing: rejected when the rear sensor is absent or
//     faulty; stopped (REAR_OBSTACLE_STOP) when the rear distance is under the threshold.
//  4. Power-on state is SAFE; drivers stay disabled until MODE DRIVE (or TEST).
//  5. Front sensors all absent/faulty count as "blocked".
//
// Decisions where the document is silent (reported to the coordinator, see firmware/README.md):
//  - A rejected DRIVE also sets the targets to zero: an older DRIVE never keeps running after the
//    Pi's newest command was refused.
//  - Hysteresis: a blockage starts when a stop/rejection happens with the distance under the
//    threshold, and is gone only when the distance is above threshold + clear_hysteresis_mm
//    (front: the nearest valid front distance; rear: the rear distance, sensor present).
//    While it lasts, forward (front) / reverse (rear) DRIVE stays rejected.
//  - OBSTACLE_STOP / REAR_OBSTACLE_STOP flags clear on the next accepted DRIVE, or as soon as the
//    blockage is gone. The event is sent once per blockage.
//  - Event arg is -1 when no valid distance exists (all front sensors absent / rear absent).
//  - The watchdog also fires once after power-on if the Pi never sends anything.
#pragma once

#include <cstdint>

#include "scr_protocol.h"

namespace scr {

struct SafetyConfig {
  uint32_t watchdog_ms = proto::kWatchdogMs;
  uint16_t obstacle_mm = 250;
  // A blockage starts below obstacle_mm and counts as gone only above obstacle_mm + this
  // (sensor noise right at the threshold must not release and re-trigger the stop).
  uint16_t clear_hysteresis_mm = 40;
};

struct SafetyEvent {
  uint8_t code;
  int16_t arg;
};

class Safety {
 public:
  explicit Safety(const SafetyConfig& c) : c_(c) {}

  void begin(uint32_t now_ms);

  // Call for every valid, known command frame (and only for those).
  void onValidFrame(uint32_t now_ms);

  // us_mm: front-left, front-centre, front-right, rear (protocol values incl. 0xFFFF / 0xFFFE).
  // Each returns the ACK status; anything but kAckOk means "rejected".
  uint8_t onDrive(int16_t left_mm_s, int16_t right_mm_s, const uint16_t us_mm[4]);
  void onStop();
  // motors_usable: the motor driver passed begin(). closed_loop_ready: encoders usable and
  // metres_per_tick calibrated.
  uint8_t onMode(uint8_t mode, bool motors_usable, bool closed_loop_ready);

  // Any other reason to stop now (e.g. encoder fault). Targets zero, ramp skipped.
  void forceStop();

  // 100 Hz: watchdog and obstacle rules.
  void tick(uint32_t now_ms, const uint16_t us_mm[4]);

  bool popEvent(SafetyEvent* out);

  // True once after anything forced the targets to zero: the caller must skip the ramp
  // (reset ramp and PID, duty zero now).
  bool takeHardStop();

  uint8_t mode() const { return mode_; }
  int16_t targetLeft() const { return left_; }
  int16_t targetRight() const { return right_; }
  bool watchdogStop() const { return watchdog_flag_; }
  bool obstacleStop() const { return obstacle_flag_; }
  bool rearObstacleStop() const { return rear_flag_; }
  bool linkLost() const { return link_lost_; }
  bool frontBlockageActive() const { return front_latch_; }
  bool rearBlockageActive() const { return rear_latch_; }

  // Helpers (public for the tests).
  static bool frontBlocked(const uint16_t us_mm[4], uint16_t threshold_mm, int16_t* nearest_mm);
  static bool rearSensing(const uint16_t us_mm[4]);
  static bool rearBlocked(const uint16_t us_mm[4], uint16_t threshold_mm);

 private:
  // Blocked right now, with hysteresis; both clear their latch when the blockage is gone.
  bool frontBlockedNow(const uint16_t us_mm[4], int16_t* nearest_mm);
  bool rearBlockedNow(const uint16_t us_mm[4]);  // absent/faulty counts as blocked
  void frontStop(int16_t nearest_mm);
  void rearStop(const uint16_t us_mm[4]);
  void zeroTargets();
  void pushEvent(uint8_t code, int16_t arg);

  SafetyConfig c_;
  uint8_t mode_ = proto::kModeSafe;
  int16_t left_ = 0;
  int16_t right_ = 0;
  uint32_t last_valid_ms_ = 0;
  bool link_lost_ = false;
  bool watchdog_flag_ = false;
  bool obstacle_flag_ = false;
  bool rear_flag_ = false;
  bool front_latch_ = false;  // a front blockage is in progress (hysteresis state)
  bool rear_latch_ = false;
  bool hard_stop_ = false;

  static constexpr uint8_t kQueue = 8;
  SafetyEvent queue_[kQueue];
  uint8_t q_head_ = 0;
  uint8_t q_count_ = 0;
};

}  // namespace scr
