// The firmware application: everything the ESP32 does, written against the HAL interfaces, so
// the same code runs on the ESP32 (real drivers), in the sim-check build and in the native tests
// (stub drivers).
//
// Two entry points with fixed rates (FreeRTOS tasks on the ESP32):
//   controlTick()    100 Hz  link receive, sensors, safety, ramps, PID, motor and servo output
//   telemetryTick()   50 Hz  one TELEMETRY frame with raw values
// The two must not run at the same time (the ESP32 main takes one mutex around each).
//
// The ESP32 sends raw values only. It never computes the pose (docs/ODOMETRY.md: the Pi does).
#pragma once

#include <cstdint>

#include "scr_control.h"
#include "scr_hal.h"
#include "scr_protocol.h"
#include "scr_safety.h"
#include "scr_ultrasonic.h"

namespace scr {

struct AppConfig {
  uint32_t control_period_ms = 10;

  SafetyConfig safety;

  // Closed-loop speed control (MODE DRIVE).
  PidGains pid;                    // input m/s, output duty -1..1
  float metres_per_tick = 0.0f;    // 0 = not calibrated: MODE DRIVE is refused (REJECTED_MODE)
  float speed_filter_alpha = 1.0f; // 0..1, 1 = no filtering of the measured speed
  bool encoder_invert[4] = {false, false, false, false};

  float ramp_mm_s2 = 0.0f;         // acceleration limit of the track speed targets
  int16_t max_speed_mm_s = 0;      // DRIVE targets are clamped to +-this

  // Open loop (MODE TEST): duty = speed target / test_full_scale_mm_s, clamped to +-test_max_duty.
  float test_full_scale_mm_s = 1000.0f;
  float test_max_duty = 0.0f;

  ServoConfig servo;
  GyroCalConfig gyro_cal;
  BatteryConfig battery;
  EncoderFaultConfig encoder_fault;
  UltrasonicConfig ultrasonic;

  uint32_t power_period_ms = 100;          // INA219 read period
  uint32_t protocol_error_report_ms = 1000;
  uint32_t still_ms = 300;                 // no encoder change for this long = "still"
};

class FirmwareApp {
 public:
  FirmwareApp(const AppConfig& config, const hal::Hardware& hw);

  // Calls begin() of every driver. A driver that reports false is never used afterwards.
  void begin(uint32_t now_ms);
  void controlTick(uint32_t now_ms);
  void telemetryTick(uint32_t now_ms);

  // For tests and diagnostics.
  const Safety& safety() const { return safety_; }
  float dutyLeft() const { return duty_left_; }
  float dutyRight() const { return duty_right_; }
  bool motorsEnabled() const { return motors_enabled_; }
  bool motorsUsable() const { return motors_ok_; }
  uint16_t flags() const;
  int32_t gyroBiasUradS() const { return gyro_cal_.biasUradS(); }
  uint32_t protocolErrors() const { return protocol_errors_total_; }

 private:
  void receive(uint32_t now_ms);
  void handleFrame(uint32_t now_ms, const proto::Message& msg, uint8_t seq);
  void readSensors(uint32_t now_ms, float dt_s);
  void stepGyroCalibration(uint32_t now_ms);
  void driveOutputs(float dt_s);
  bool moving(uint32_t now_ms) const;
  bool encoderFaultLatched() const;
  void send(const proto::Message& msg);
  void sendEvent(uint32_t now_ms, uint8_t code, int16_t arg);
  void sendAck(uint8_t type, uint8_t seq, uint8_t status);

  AppConfig cfg_;
  hal::Hardware hw_;

  proto::FrameDecoder decoder_;
  uint8_t tx_seq_ = 0;
  uint32_t protocol_errors_total_ = 0;
  uint32_t protocol_errors_unreported_ = 0;
  uint32_t last_error_report_ms_ = 0;

  Safety safety_;
  Pid pid_left_;
  Pid pid_right_;
  RampLimiter ramp_left_;
  RampLimiter ramp_right_;
  ServoController servo_;
  GyroBiasCalibrator gyro_cal_;
  BatteryMonitor battery_;
  EncoderFaultDetector enc_fault_[4];
  UltrasonicScheduler ultrasonic_;

  bool motors_ok_ = false;
  bool encoders_ok_ = false;
  bool imu_present_ = false;  // begin() succeeded
  bool imu_ok_ = false;       // last read succeeded
  bool servo_ok_ = false;
  bool power_present_ = false;
  bool power_ok_ = false;

  bool started_ = false;
  uint32_t last_tick_ms_ = 0;
  uint32_t last_power_ms_ = 0;
  uint32_t last_enc_change_ms_ = 0;
  bool enc_changed_this_tick_ = false;

  int32_t enc_[4] = {0, 0, 0, 0};
  int32_t enc_delta_[4] = {0, 0, 0, 0};
  bool have_enc_ = false;
  float speed_left_m_s_ = 0.0f;
  float speed_right_m_s_ = 0.0f;
  hal::ImuSample imu_{};
  uint16_t battery_mv_ = 0;
  int16_t battery_ma_ = 0;

  float duty_left_ = 0.0f;
  float duty_right_ = 0.0f;
  bool motors_enabled_ = false;
};

}  // namespace scr
