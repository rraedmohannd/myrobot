#include "scr_control.h"

namespace scr {

namespace {
float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
}  // namespace

// ---- Pid ------------------------------------------------------------------------------------
void Pid::reset() {
  integral_ = 0.0f;
  prev_measured_ = 0.0f;
  have_prev_ = false;
}

float Pid::update(float setpoint, float measured, float dt_s) {
  if (dt_s <= 0.0f) return 0.0f;
  const float error = setpoint - measured;
  const float derivative = have_prev_ ? -(measured - prev_measured_) / dt_s : 0.0f;
  prev_measured_ = measured;
  have_prev_ = true;

  const float candidate_integral = integral_ + g_.ki * error * dt_s;
  const float base = g_.kff * setpoint + g_.kp * error + g_.kd * derivative;
  float out = base + candidate_integral;
  if (out > g_.out_max) {
    if (error < 0.0f) integral_ = candidate_integral;  // only allow unwinding
    out = g_.out_max;
  } else if (out < g_.out_min) {
    if (error > 0.0f) integral_ = candidate_integral;
    out = g_.out_min;
  } else {
    integral_ = candidate_integral;
  }
  return out;
}

// ---- RampLimiter ----------------------------------------------------------------------------
float RampLimiter::update(float target, float dt_s) {
  const float step = max_rate_ * dt_s;
  if (step <= 0.0f) return value_;
  const float diff = target - value_;
  if (diff > step) {
    value_ += step;
  } else if (diff < -step) {
    value_ -= step;
  } else {
    value_ = target;
  }
  return value_;
}

// ---- ServoController ------------------------------------------------------------------------
void ServoController::setConfig(const ServoConfig& c) {
  c_ = c;
  if (commanded_) setTarget(target_);
  const float lo = static_cast<float>(c_.min_cdeg);
  const float hi = static_cast<float>(c_.max_cdeg);
  if (current_ < lo) current_ = lo;
  if (current_ > hi) current_ = hi;
}

void ServoController::setTarget(int16_t pan_cdeg) {
  if (pan_cdeg < c_.min_cdeg) pan_cdeg = c_.min_cdeg;
  if (pan_cdeg > c_.max_cdeg) pan_cdeg = c_.max_cdeg;
  target_ = pan_cdeg;
  commanded_ = true;
}

void ServoController::update(float dt_s) {
  const float step = c_.slew_cdeg_per_s * dt_s;
  if (step <= 0.0f) return;
  const float diff = static_cast<float>(target_) - current_;
  if (diff > step) {
    current_ += step;
  } else if (diff < -step) {
    current_ -= step;
  } else {
    current_ = static_cast<float>(target_);
  }
}

uint16_t ServoController::pulseUs() const {
  const float deg = current_ / 100.0f;
  const float sign = c_.invert ? -1.0f : 1.0f;
  const float us = c_.center_pulse_us + sign * deg * c_.us_per_deg;
  return static_cast<uint16_t>(
      clampf(us, static_cast<float>(c_.pulse_min_us), static_cast<float>(c_.pulse_max_us)) + 0.5f);
}

// ---- GyroBiasCalibrator ---------------------------------------------------------------------
void GyroBiasCalibrator::start() {
  state_ = State::Running;
  abort_ = false;
  count_ = 0;
  sum_ = 0;
  min_ = 0;
  max_ = 0;
}

void GyroBiasCalibrator::abort() {
  if (state_ == State::Running) abort_ = true;
}

GyroBiasCalibrator::State GyroBiasCalibrator::addSample(int32_t raw_urad_s, bool moved) {
  if (state_ != State::Running) return State::Idle;
  if (abort_ || moved) {
    state_ = State::Idle;
    return State::Failed;
  }
  if (count_ == 0) {
    min_ = raw_urad_s;
    max_ = raw_urad_s;
  } else {
    if (raw_urad_s < min_) min_ = raw_urad_s;
    if (raw_urad_s > max_) max_ = raw_urad_s;
  }
  sum_ += raw_urad_s;
  ++count_;
  if (static_cast<int64_t>(max_) - static_cast<int64_t>(min_) > c_.max_spread_urad_s) {
    state_ = State::Idle;
    return State::Failed;
  }
  if (count_ >= c_.samples) {
    bias_ = static_cast<int32_t>(sum_ / static_cast<int64_t>(count_));
    state_ = State::Idle;
    return State::Done;
  }
  return State::Running;
}

// ---- BatteryMonitor -------------------------------------------------------------------------
bool BatteryMonitor::update(uint32_t now_ms, uint16_t mv, bool sensor_ok) {
  if (!sensor_ok || c_.low_mv == 0) {
    below_ = false;
    return false;
  }
  if (low_) {
    if (mv >= c_.recover_mv) {
      low_ = false;
      below_ = false;
    }
    return false;
  }
  if (mv < c_.low_mv) {
    if (!below_) {
      below_ = true;
      below_since_ms_ = now_ms;
    }
    if (now_ms - below_since_ms_ >= c_.hold_ms) {
      low_ = true;
      return true;
    }
  } else {
    below_ = false;
  }
  return false;
}

// ---- EncoderFaultDetector -------------------------------------------------------------------
bool EncoderFaultDetector::update(uint32_t now_ms, float duty, int32_t delta_ticks) {
  if (c_.timeout_ms == 0) return false;
  const float mag = duty < 0.0f ? -duty : duty;
  if (delta_ticks != 0) {
    timing_ = false;
    faulty_ = false;
    return false;
  }
  if (mag < c_.min_duty) {
    timing_ = false;
    return false;
  }
  if (!timing_) {
    timing_ = true;
    since_ms_ = now_ms;
    return false;
  }
  if (!faulty_ && now_ms - since_ms_ >= c_.timeout_ms) {
    faulty_ = true;
    return true;
  }
  return false;
}

// ---- VoltageCompensator ---------------------------------------------------------------------
void VoltageCompensator::update(float volts, bool valid, float dt_s) {
  if (!valid || !(volts >= kPlausibleMinV && volts <= kPlausibleMaxV)) {
    have_ = false;  // back to the conservative full-pack constant
    v_ = kFullPackV;
    return;
  }
  if (!have_ || volts >= v_) {
    v_ = volts;  // first reading or a rise: follow at once (lower cap)
  } else {
    const float tau = c_.fall_tau_s > 0.0f ? c_.fall_tau_s : 0.0f;
    const float a = tau > 0.0f ? (dt_s / (tau + dt_s)) : 1.0f;
    v_ += a * (volts - v_);  // a fall: slowly
  }
  have_ = true;
}

float VoltageCompensator::dutyCap() const {
  float absolute = c_.motor_max_duty;
  if (!(absolute > 0.0f)) return 0.0f;  // also catches NaN
  if (absolute > 1.0f) absolute = 1.0f;
  float headroom = filteredVolts() - c_.v_driver_drop;
  if (!(headroom > kMinHeadroomV)) headroom = kMinHeadroomV;
  float cap = c_.motor_rated_v / headroom;
  if (!(cap > 0.0f)) cap = 0.0f;
  return cap < absolute ? cap : absolute;
}

// ---- SlipDetector ---------------------------------------------------------------------------
bool SlipDetector::update(uint32_t now_ms, int32_t front_delta, int32_t rear_delta) {
  if (!started_) {
    started_ = true;
    window_start_ms_ = now_ms;
  }
  front_ += front_delta;
  rear_ += rear_delta;
  if (now_ms - window_start_ms_ < c_.window_ms) return false;
  const int32_t f = front_ < 0 ? -front_ : front_;
  const int32_t r = rear_ < 0 ? -rear_ : rear_;
  const int32_t m = f > r ? f : r;
  int64_t diff = static_cast<int64_t>(front_) - static_cast<int64_t>(rear_);
  if (diff < 0) diff = -diff;
  front_ = 0;
  rear_ = 0;
  window_start_ms_ = now_ms;
  const bool slip = m > c_.min_ticks && static_cast<float>(diff) > c_.ratio * static_cast<float>(m);
  if (slip && !latched_) {
    latched_ = true;
    return true;
  }
  if (!slip) latched_ = false;  // re-armed
  return false;
}

// ---- PCNT -----------------------------------------------------------------------------------
int32_t pcntDelta(int16_t previous, int16_t now, int16_t limit) {
  const int32_t lim = limit;
  int32_t d = static_cast<int32_t>(now) - static_cast<int32_t>(previous);
  if (d > lim / 2) d -= lim;   // wrapped downward through -limit -> 0
  if (d < -(lim / 2)) d += lim;  // wrapped upward through +limit -> 0
  return d;
}

int32_t wrapAdd(int32_t total, int32_t delta) {
  return static_cast<int32_t>(static_cast<uint32_t>(total) + static_cast<uint32_t>(delta));
}

// ---- SoftTaskWatchdog -----------------------------------------------------------------------
int SoftTaskWatchdog::add(uint32_t now_ms) {
  if (count_ >= 4) return -1;
  last_[count_] = now_ms;
  return count_++;
}

void SoftTaskWatchdog::feed(int id, uint32_t now_ms) {
  if (id >= 0 && id < count_) last_[id] = now_ms;
}

int SoftTaskWatchdog::expired(uint32_t now_ms) const {
  for (int i = 0; i < count_; ++i) {
    if (now_ms - last_[i] >= timeout_ms_) return i;
  }
  return -1;
}

}  // namespace scr
