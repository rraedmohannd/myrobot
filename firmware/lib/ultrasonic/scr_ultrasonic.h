// HC-SR04 scheduling: one sensor per time slot (staggered triggering, so a sensor never hears
// another sensor's ping), echo timeout, absent/faulty detection. Pure logic on top of
// hal::IUltrasonic.
#pragma once

#include <cstdint>

#include "scr_hal.h"
#include "scr_protocol.h"

namespace scr {

struct UltrasonicConfig {
  uint32_t slot_ms = 30;               // one sensor is triggered per slot; must exceed the echo timeout
  uint8_t order[4] = {0, 2, 1, 3};     // firing order (indices: FL, FC, FR, rear)
  uint8_t fault_after_misses = 3;      // consecutive "echo never started" before a sensor is absent
  uint16_t max_range_mm = 4000;        // longer echoes are reported as "no echo"
  float mm_per_us = 0.1715f;           // half the speed of sound (343 m/s at 20 degC), physics not calibration
};

class UltrasonicScheduler {
 public:
  UltrasonicScheduler(const UltrasonicConfig& c, hal::IUltrasonic* hw);

  // hw_ok = false: every sensor is reported absent and nothing is triggered.
  void begin(bool hw_ok);
  void tick(uint32_t now_ms);  // call at the control rate

  // Protocol values: mm, 0xFFFF = no echo, 0xFFFE = absent or faulty.
  // A sensor is "absent" until its first completed measurement (unknown = not safe).
  const uint16_t* distancesMm() const { return mm_; }
  bool anyFault() const;
  int8_t activeSensor() const { return active_; }
  uint8_t misses(uint8_t i) const { return misses_[i & 3]; }  // consecutive "echo never started"
  bool everAnswered(uint8_t i) const { return answered_[i & 3]; }  // echo started at least once
  uint32_t rounds() const { return rounds_; }  // +1 each time the firing order starts over

 private:
  int8_t nextPresent();

  UltrasonicConfig c_;
  hal::IUltrasonic* hw_;
  bool hw_ok_ = false;
  uint16_t mm_[4];
  uint8_t misses_[4];
  bool answered_[4] = {false, false, false, false};
  uint32_t rounds_ = 0;
  int8_t active_ = -1;
  uint8_t order_pos_ = 0;
  uint32_t trigger_ms_ = 0;
};

}  // namespace scr
