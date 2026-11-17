// Small hardware-independent control blocks: PID, ramp limiter, servo slew/limits,
// gyro bias calibration, battery monitor, encoder fault detector.
// No Arduino headers: everything here compiles and is unit-tested natively.
#pragma once

#include <cstdint>

namespace scr {

// ---------------------------------------------------------------------------------------------
// PID with feed-forward, output clamp and anti-windup (the integrator is not advanced while the
// output is saturated in the same direction). Derivative acts on the measurement.
struct PidGains {
  float kp = 0.0f;
  float ki = 0.0f;
  float kd = 0.0f;
  float kff = 0.0f;       // output per unit of setpoint
  float out_min = -1.0f;
  float out_max = 1.0f;
};

class Pid {
 public:
  Pid() = default;
  explicit Pid(const PidGains& g) : g_(g) {}
  void setGains(const PidGains& g) { g_ = g; }
  void reset();
  float update(float setpoint, float measured, float dt_s);
  float integral() const { return integral_; }

 private:
  PidGains g_{};
  float integral_ = 0.0f;
  float prev_measured_ = 0.0f;
  bool have_prev_ = false;
};

// ---------------------------------------------------------------------------------------------
// Limits how fast a value may change (units per second), in both directions.
class RampLimiter {
 public:
  RampLimiter() = default;
  explicit RampLimiter(float max_rate_per_s) : max_rate_(max_rate_per_s) {}
  void setMaxRate(float max_rate_per_s) { max_rate_ = max_rate_per_s; }
  void reset(float value = 0.0f) { value_ = value; }  // jump (used by every stop)
  float update(float target, float dt_s);
  float value() const { return value_; }

 private:
  float max_rate_ = 0.0f;  // 0 = no movement at all (safe placeholder)
  float value_ = 0.0f;
};

// ---------------------------------------------------------------------------------------------
// Pan servo: clamps the target to the angle limits, limits the slew rate, converts to a pulse.
struct ServoConfig {
  int16_t min_cdeg = 0;          // right limit (negative angles = right)
  int16_t max_cdeg = 0;          // left limit
  float slew_cdeg_per_s = 0.0f;
  float center_pulse_us = 1500.0f;
  float us_per_deg = 0.0f;       // pulse change per degree
  bool invert = false;           // true when a longer pulse turns the camera right
  uint16_t pulse_min_us = 1500;  // hard pulse limits, whatever the angle maths says
  uint16_t pulse_max_us = 1500;
};

class ServoController {
 public:
  explicit ServoController(const ServoConfig& c) : c_(c) {}
  void setTarget(int16_t pan_cdeg);  // clamped to the limits
  void update(float dt_s);           // moves the current angle toward the target
  int16_t targetCdeg() const { return target_; }
  float currentCdeg() const { return current_; }
  uint16_t pulseUs() const;
  bool commanded() const { return commanded_; }  // false until the first setTarget

 private:
  ServoConfig c_;
  int16_t target_ = 0;
  float current_ = 0.0f;
  bool commanded_ = false;
};

// ---------------------------------------------------------------------------------------------
// Gyro z bias calibration: average the raw rate for a fixed number of samples while the robot is
// still. Fails when the robot moved (encoder change reported by the caller, or the raw rate
// spread larger than the limit).
struct GyroCalConfig {
  uint16_t samples = 200;            // 2 s at 100 Hz
  int32_t max_spread_urad_s = 0;     // max - min of the raw rate that still counts as "still"
};

class GyroBiasCalibrator {
 public:
  enum class State : uint8_t { Idle, Running, Done, Failed };

  explicit GyroBiasCalibrator(const GyroCalConfig& c) : c_(c) {}
  void start();
  // Feed one raw sample (bias NOT removed). `moved` = an encoder changed or a drive is active.
  // Returns the state after this sample; Done/Failed are returned exactly once, then Idle.
  State addSample(int32_t raw_urad_s, bool moved);
  void abort();  // -> Failed on the next addSample (e.g. IMU read error)
  bool running() const { return state_ == State::Running; }
  int32_t biasUradS() const { return bias_; }  // last successful result (0 before any)

 private:
  GyroCalConfig c_;
  State state_ = State::Idle;
  bool abort_ = false;
  uint16_t count_ = 0;
  int64_t sum_ = 0;
  int32_t min_ = 0;
  int32_t max_ = 0;
  int32_t bias_ = 0;
};

// ---------------------------------------------------------------------------------------------
// Low-battery detection with a hold time and hysteresis.
struct BatteryConfig {
  uint16_t low_mv = 0;          // 0 = detection off
  uint16_t recover_mv = 0;      // flag clears above this
  uint32_t hold_ms = 0;         // must stay low this long
};

class BatteryMonitor {
 public:
  explicit BatteryMonitor(const BatteryConfig& c) : c_(c) {}
  // Returns true exactly once, when the low state is entered.
  bool update(uint32_t now_ms, uint16_t mv, bool sensor_ok);
  bool low() const { return low_; }

 private:
  BatteryConfig c_;
  bool low_ = false;
  bool below_ = false;
  uint32_t below_since_ms_ = 0;
};

// ---------------------------------------------------------------------------------------------
// Encoder fault: a track is driven above a duty threshold for some time and one of its encoders
// does not count at all.
struct EncoderFaultConfig {
  float min_duty = 1.0f;     // |duty| above this counts as "driven"
  uint32_t timeout_ms = 0;   // 0 = detection off
};

class EncoderFaultDetector {
 public:
  explicit EncoderFaultDetector(const EncoderFaultConfig& c) : c_(c) {}
  // duty of the track this encoder belongs to; delta = ticks since the last call.
  // Returns true exactly once per fault (re-armed when the encoder counts again).
  bool update(uint32_t now_ms, float duty, int32_t delta_ticks);
  bool faulty() const { return faulty_; }

 private:
  EncoderFaultConfig c_;
  bool timing_ = false;
  bool faulty_ = false;
  uint32_t since_ms_ = 0;
};

}  // namespace scr
