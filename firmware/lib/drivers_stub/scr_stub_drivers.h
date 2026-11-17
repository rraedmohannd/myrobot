// STUB drivers for the native tests and the sim-check build. They touch no hardware.
// Nothing measured with them is a physical result (CLAUDE.md rule 5): they exist to compile and
// exercise the application logic off-target.
#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

#include "scr_hal.h"

namespace scr {
namespace stub {

class StubMotors : public hal::IMotorDriver {
 public:
  bool begin_result = true;
  bool begun = false;
  bool enabled = false;
  float left = 0.0f;
  float right = 0.0f;
  int enable_calls = 0;

  bool begin() override {
    begun = true;
    return begin_result;
  }
  void setEnabled(bool e) override {
    enabled = e;
    ++enable_calls;
  }
  void setTrackDuty(float l, float r) override {
    left = l;
    right = r;
  }
};

class StubEncoders : public hal::IEncoders {
 public:
  bool begin_result = true;
  int32_t counts[4] = {0, 0, 0, 0};

  bool begin() override { return begin_result; }
  void read(int32_t out[4]) override {
    for (int i = 0; i < 4; ++i) out[i] = counts[i];
  }
};

class StubImu : public hal::IImu {
 public:
  bool begin_result = true;
  bool read_result = true;
  hal::ImuSample sample{0, {0, 0, 1000}};

  bool begin() override { return begin_result; }
  bool read(hal::ImuSample* out) override {
    if (!read_result) return false;
    *out = sample;
    return true;
  }
};

class StubUltrasonic : public hal::IUltrasonic {
 public:
  bool begin_result = true;
  bool is_present[4] = {true, true, true, true};
  hal::EchoState state[4] = {hal::EchoState::Timeout, hal::EchoState::Timeout,
                             hal::EchoState::Timeout, hal::EchoState::Timeout};
  uint32_t echo_us[4] = {0, 0, 0, 0};
  std::vector<uint8_t> trigger_log;  // order in which the sensors were fired
  int pending = -1;                  // sensor triggered and not collected yet
  int overlap_errors = 0;            // a trigger while another sensor was still pending

  bool begin() override { return begin_result; }
  bool present(uint8_t i) const override { return is_present[i]; }
  void trigger(uint8_t i) override {
    if (pending >= 0) ++overlap_errors;
    pending = i;
    trigger_log.push_back(i);
  }
  hal::EchoState collect(uint8_t i, uint32_t* us) override {
    if (pending == i) pending = -1;
    *us = echo_us[i];
    return state[i];
  }
  // Convenience: make sensor i report a distance.
  void setDistanceMm(uint8_t i, float mm) {
    state[i] = hal::EchoState::Done;
    echo_us[i] = static_cast<uint32_t>(mm / 0.1715f + 0.5f);
  }
  void setNoEcho(uint8_t i) { state[i] = hal::EchoState::Timeout; }
  void setDead(uint8_t i) { state[i] = hal::EchoState::NoRise; }
};

class StubServo : public hal::IServo {
 public:
  bool begin_result = true;
  uint16_t pulse_us = 0;
  int writes = 0;

  bool begin() override { return begin_result; }
  void writePulseUs(uint16_t us) override {
    pulse_us = us;
    ++writes;
  }
};

class StubPower : public hal::IPowerSensor {
 public:
  bool begin_result = true;
  bool read_result = true;
  uint16_t mv = 12000;
  int16_t ma = 500;

  bool begin() override { return begin_result; }
  bool read(uint16_t* bus_mv, int16_t* current_ma) override {
    if (!read_result) return false;
    *bus_mv = mv;
    *current_ma = ma;
    return true;
  }
};

class StubLink : public hal::ILinkPort {
 public:
  std::deque<uint8_t> rx;   // bytes "from the Pi"
  std::vector<uint8_t> tx;  // bytes the firmware wrote

  size_t read(uint8_t* buf, size_t cap) override {
    size_t n = 0;
    while (n < cap && !rx.empty()) {
      buf[n++] = rx.front();
      rx.pop_front();
    }
    return n;
  }
  void write(const uint8_t* data, size_t len) override { tx.insert(tx.end(), data, data + len); }
  void inject(const uint8_t* data, size_t len) { rx.insert(rx.end(), data, data + len); }
};

// All stubs together, plus a crude SIMULATED track model (duty -> encoder ticks) so that the
// closed loop has something to act on. Not a model of the Rover 5.
struct StubRobot {
  StubMotors motors;
  StubEncoders encoders;
  StubImu imu;
  StubUltrasonic ultrasonic;
  StubServo servo;
  StubPower power;
  StubLink link;

  float sim_ticks_per_s_at_full_duty = 4000.0f;  // arbitrary
  float sim_pos[4] = {0, 0, 0, 0};

  hal::Hardware hardware() {
    hal::Hardware hw;
    hw.motors = &motors;
    hw.encoders = &encoders;
    hw.imu = &imu;
    hw.ultrasonic = &ultrasonic;
    hw.servo = &servo;
    hw.power = &power;
    hw.link = &link;
    return hw;
  }

  // Advance the simulated tracks by dt seconds with the duty the firmware last wrote.
  void step(float dt_s) {
    const float l = motors.enabled ? motors.left : 0.0f;
    const float r = motors.enabled ? motors.right : 0.0f;
    for (int i = 0; i < 4; ++i) {
      sim_pos[i] += (i < 2 ? l : r) * sim_ticks_per_s_at_full_duty * dt_s;
      encoders.counts[i] = static_cast<int32_t>(sim_pos[i]);
    }
  }
};

}  // namespace stub
}  // namespace scr
