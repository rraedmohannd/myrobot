// The firmware application: everything the ESP32 does, written against the HAL interfaces, so
// the same code runs on the ESP32 (real drivers), in the sim-check build and in the native tests
// (stub drivers).
//
// Two entry points with fixed rates (FreeRTOS tasks on the ESP32):
//   controlTick()    100 Hz  link receive, sensors, safety, ramps, PID, motor and servo output
//   telemetryTick()   50 Hz  one TELEMETRY frame with raw values
// The two must not run at the same time (the ESP32 main takes one mutex around each).
// Neither ever waits for I2C: on the ESP32 the I2C devices are read by their own task and the
// drivers here only copy the latest values (hal::IImu, hal::IPowerSensor).
//
// The ESP32 sends raw values only. It never computes the pose (docs/ODOMETRY.md: the Pi does).
#pragma once

#include <cstdint>

#include "scr_control.h"
#include "scr_hal.h"
#include "scr_params.h"
#include "scr_protocol.h"
#include "scr_safety.h"
#include "scr_ultrasonic.h"

namespace scr {

// Run-time configuration. The fields marked [param] are runtime parameters (params.json): the
// values given here become the "compiled defaults" of the parameter store (cfg::makeAppConfig()
// sets them from the generated table; tests may use other values), and from then on the store
// is the only source: PARAM_SET / NVS -> ParamStore -> paramsToConfig() -> these fields.
struct AppConfig {
  uint32_t control_period_ms = 10;

  SafetyConfig safety;             // [param] obstacle_mm, clear_hysteresis_mm

  // Closed-loop speed control (MODE DRIVE).
  PidGains pid;                    // [param] kp, ki, kd, kff (input m/s, output duty)
  float metres_per_tick = 0.0f;    // [param] 0 = not calibrated: MODE DRIVE is refused
  float speed_filter_alpha = 1.0f; // [param] 0..1, 1 = no filtering of the measured speed
  bool encoder_invert[4] = {false, false, false, false};  // [param]

  float ramp_mm_s2 = 0.0f;         // [param] acceleration limit of the track speed targets
  int16_t max_speed_mm_s = 0;      // [param] DRIVE targets are clamped to +-this

  // Open loop (MODE TEST): duty = speed target / test_full_scale_mm_s, clamped to +-test_max_duty.
  float test_full_scale_mm_s = 1000.0f;
  float test_max_duty = 0.0f;      // [param]

  // Motor output (interface spec A8). The struct default 1.0 is for the tests; the firmware's
  // compiled default comes from params.json (0 = nothing moves).
  float motor_max_duty = 1.0f;     // [param]
  float motor_rated_v = 7.2f;      // [param]
  float v_driver_drop = 2.0f;      // [param]
  bool motor_invert_left = false;  // [param]
  bool motor_invert_right = false; // [param]

  ServoConfig servo;               // [param] all fields
  GyroCalConfig gyro_cal;
  float gyro_scale = 1.0f;         // [param]
  BatteryConfig battery;           // [param] low_mv, recover_mv
  uint16_t battery_cutoff_mv = 0;  // [param] 0 = hard cut off
  uint32_t battery_cutoff_hold_ms = 2000;      // must stay below this long (load sag is short)
  uint16_t battery_cutoff_recover_mv = 300;    // cut-off clears above cutoff + this
  float ina219_shunt_ohms = 0.1f;  // [param]
  float battery_voltage_scale = 1.0f;  // [param]
  float battery_current_scale = 1.0f;  // [param]
  bool ina219_invert_current = false;  // [param]
  EncoderFaultConfig encoder_fault;    // [param]
  SlipConfig slip;                     // [param] ratio, min_ticks
  UltrasonicConfig ultrasonic;

  uint32_t power_period_ms = 100;          // INA219 read period
  uint32_t protocol_error_report_ms = 1000;
  uint32_t still_ms = 300;                 // no encoder change for this long = "still"
  float busy_speed_m_s = 0.010f;           // PARAM_SET is BUSY above this measured track speed
  uint32_t diag_scan_wait_ms = 250;        // DIAG_REPORT waits at most this long for the I2C scan
  // Speed-sign mismatch fault (MODE DRIVE only), see FirmwareApp::checkSpeedSign.
  float sign_fault_min_duty = 0.2f;
  float sign_fault_min_speed_m_s = 0.050f;
  uint32_t sign_fault_ms = 300;

  // Build identity (DIAG_REPORT), set by the main of each build.
  uint8_t build_env = proto::kBuildNativeTest;
  uint8_t driver_type = 0;          // 0 L298N, 1 VNH
  uint8_t static_diag_flags = 0;    // PINS_CONFIRMED, CI_PINS_DO_NOT_FLASH
  // CI pin build (SCR_CI_PINS_DO_NOT_FLASH): MODE DRIVE/TEST, SERVO and BRINGUP are always
  // refused; the robot stays in SAFE for ever.
  bool permanent_safe = false;
};

// The [param] fields of AppConfig <-> the parameter store.
// Returns the number of values that failed the compiled bounds (they keep the table default).
int seedParamsFromConfig(const AppConfig& c, ParamStore* p);
void paramsToConfig(const ParamStore& p, AppConfig* c);

class FirmwareApp {
 public:
  FirmwareApp(const AppConfig& config, const hal::Hardware& hw);

  // Calls begin() of every driver. A driver that reports false is never used afterwards.
  // Loads the parameters from storage, sends EVENT BOOT and then (when the I2C scan is done)
  // DIAG_REPORT.
  void begin(uint32_t now_ms);
  void controlTick(uint32_t now_ms);
  void telemetryTick(uint32_t now_ms);

  // For tests and diagnostics.
  const Safety& safety() const { return safety_; }
  const ParamStore& params() const { return params_; }
  int seedFailures() const { return seed_failures_; }
  float dutyLeft() const { return duty_left_; }
  float dutyRight() const { return duty_right_; }
  float dutyCap() const { return vcomp_.dutyCap(); }
  bool motorsEnabled() const { return motors_enabled_; }
  bool motorsUsable() const { return motors_ok_; }
  uint16_t flags() const;
  int32_t gyroBiasUradS() const { return gyro_cal_.biasUradS(); }
  uint32_t protocolErrors() const { return protocol_errors_total_; }
  bool bringupActive() const { return imu_raw_period_ms_ != 0 || us_raw_on_ || jog_active_; }
  bool batteryCutoff() const { return cutoff_; }
  uint16_t servoJogPulseUs() const { return jog_pulse_us_; }
  const AppConfig& config() const { return cfg_; }

 private:
  void applyParams();
  void receive(uint32_t now_ms);
  void handleFrame(uint32_t now_ms, const proto::Message& msg, uint8_t seq);
  void handleParamSet(uint32_t now_ms, const proto::ParamSet& m, uint8_t seq);
  void handleParamGet(const proto::ParamGet& m, uint8_t seq);
  void handleParamSave(uint32_t now_ms, uint8_t seq);
  void handleBringup(uint32_t now_ms, const proto::Bringup& m, uint8_t seq);
  void sendParamReport(int index);
  void sendDiag(uint32_t now_ms);
  void readSensors(uint32_t now_ms, float dt_s);
  void readPower(uint32_t now_ms);
  void stepGyroCalibration(uint32_t now_ms);
  void stepBringup(uint32_t now_ms);
  void endBringup();
  void stepParamSave();
  void checkSpeedSign(uint32_t now_ms);
  void driveOutputs(float dt_s);
  bool moving(uint32_t now_ms) const;
  bool tracksMoving() const;
  bool encoderFaultLatched() const;
  void send(const proto::Message& msg);
  void sendEvent(uint32_t now_ms, uint8_t code, int16_t arg);
  void sendAck(uint8_t type, uint8_t seq, uint8_t status);

  AppConfig cfg_;
  hal::Hardware hw_;
  ParamStore params_;
  int seed_failures_ = 0;

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
  SlipDetector slip_[2];
  VoltageCompensator vcomp_;
  UltrasonicScheduler ultrasonic_;

  bool motors_ok_ = false;
  bool encoders_ok_ = false;
  bool imu_present_ = false;  // begin() succeeded
  bool imu_ok_ = false;       // last read succeeded
  bool servo_ok_ = false;
  bool power_present_ = false;
  bool power_ok_ = false;
  bool storage_ok_ = false;

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

  // Battery hard cut-off (BATTERY_CUTOFF_MV).
  bool cutoff_ = false;
  bool cutoff_below_ = false;
  uint32_t cutoff_below_since_ms_ = 0;

  // PARAM_SAVE in steps; speed-sign mismatch timers.
  uint8_t save_seq_ = 0;
  bool sign_timing_[2] = {false, false};
  uint32_t sign_since_ms_[2] = {0, 0};

  // Diagnostics.
  bool diag_pending_ = false;
  uint32_t diag_deadline_ms_ = 0;

  // Bring-up (MODE TEST only).
  uint32_t imu_raw_period_ms_ = 0;  // 0 = IMU_RAW stream off
  uint32_t last_imu_raw_ms_ = 0;
  bool us_raw_on_ = false;
  uint32_t last_us_round_ = 0;
  bool jog_active_ = false;
  bool jog_hold_ = false;  // after a jog: the servo keeps the jog pulse until the next SERVO
  uint16_t jog_pulse_us_ = 0;
  uint16_t last_servo_pulse_us_ = 0;  // 0 = never written

  float duty_left_ = 0.0f;
  float duty_right_ = 0.0f;
  bool motors_enabled_ = false;
};

}  // namespace scr
