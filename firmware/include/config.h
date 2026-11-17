// All tunables of the firmware, in one place.
//
// !!!  EVERY VALUE MARKED "PLACEHOLDER" IS A PLACEHOLDER UNTIL MEASURED / CALIBRATED  !!!
// Nothing here was measured on the robot: the hardware did not exist when this was written.
// The placeholders are chosen so that an uncalibrated robot does NOT move:
//   - METRES_PER_TICK = 0  -> MODE DRIVE (closed loop) is refused with REJECTED_MODE;
//   - PID gains = 0        -> zero duty even if someone only sets METRES_PER_TICK;
//   - RAMP / MAX_SPEED / TEST_MAX_DUTY = 0 -> zero duty in TEST mode too.
// The student raises them step by step (firmware/README.md, "First power-on").
//
// No pin numbers here: pins live only in include/pins.h (CLAUDE.md rule 8).
#pragma once

#include "scr_app.h"

// ---- Motor driver type: compile-time choice (OPEN QUESTION, see README) ----------------------
// docs/SPRINT_4.md says 2 x dual VNH shields, docs/HARDWARE.md says 2 x L298N. Both take one PWM
// and two direction signals per motor, so only the profile below differs. Define exactly one.
#if !defined(SCR_MOTOR_DRIVER_VNH) && !defined(SCR_MOTOR_DRIVER_L298N)
#define SCR_MOTOR_DRIVER_L298N 1  // PLACEHOLDER choice: docs/HARDWARE.md is the "confirmed parts" list
#endif
#if defined(SCR_MOTOR_DRIVER_VNH) && defined(SCR_MOTOR_DRIVER_L298N)
#error "Define only one of SCR_MOTOR_DRIVER_VNH / SCR_MOTOR_DRIVER_L298N"
#endif

namespace scr {
namespace cfg {

// ---- Fixed by docs/SERIAL_PROTOCOL.md (not tunable) ------------------------------------------
constexpr uint32_t CONTROL_PERIOD_MS = 10;    // 100 Hz
constexpr uint32_t TELEMETRY_PERIOD_MS = 20;  // 50 Hz
constexpr uint32_t WATCHDOG_MS = 300;

// ---- Safety ----------------------------------------------------------------------------------
constexpr uint16_t OBSTACLE_THRESHOLD_MM = 250;  // default from the protocol document; tune on the robot
// PLACEHOLDER to tune on the robot: a blockage (front or rear) counts as gone only when the
// distance is above OBSTACLE_THRESHOLD_MM + this. Same default as the simulated ESP32 of robotd.
constexpr uint16_t OBSTACLE_CLEAR_HYSTERESIS_MM = 40;

// ---- Calibration constants (docs/ODOMETRY.md section 5) --------------------------------------
// PLACEHOLDER: 0 = NOT CALIBRATED. Measure: drive 3 m straight x 5 runs, tape-measure,
// metres_per_tick = distance / mean ticks of the four encoders.
constexpr float METRES_PER_TICK = 0.0f;
// PLACEHOLDER, NOT USED BY THE FIRMWARE (the pose is computed on the Pi). Kept here only so the
// two calibration constants are written down in one place; the value that matters is robotd's.
constexpr float TRACK_WIDTH_M = 0.0f;
// PLACEHOLDER: set true for an encoder that counts down when its track runs forward
// (order: front-left, rear-left, front-right, rear-right). Check in TEST mode, tracks off the ground.
constexpr bool ENCODER_INVERT[4] = {false, false, false, false};

// ---- Speed control ---------------------------------------------------------------------------
// PLACEHOLDER gains, all zero = no output. Tune on the robot (input m/s, output duty -1..1).
constexpr float PID_KP = 0.0f;
constexpr float PID_KI = 0.0f;
constexpr float PID_KD = 0.0f;
constexpr float PID_KFF = 0.0f;              // feed-forward: duty per (m/s)
constexpr float SPEED_FILTER_ALPHA = 1.0f;   // PLACEHOLDER: 1 = no low-pass on the measured speed
constexpr float RAMP_MM_S2 = 0.0f;           // PLACEHOLDER: acceleration limit; 0 = targets never rise
constexpr int16_t MAX_SPEED_MM_S = 0;        // PLACEHOLDER: DRIVE targets are clamped to +-this

// ---- Open-loop TEST mode ---------------------------------------------------------------------
constexpr float TEST_FULL_SCALE_MM_S = 1000.0f;  // a DRIVE of this many mm/s means duty 1.0 (a convention, not a measurement)
constexpr float TEST_MAX_DUTY = 0.0f;            // PLACEHOLDER: cap of the open-loop duty; 0 = TEST mode drives nothing

// ---- Motor PWM (depends on the driver type) --------------------------------------------------
#if defined(SCR_MOTOR_DRIVER_VNH)
constexpr uint32_t MOTOR_PWM_HZ = 16000;  // PLACEHOLDER: check the limit of the exact VNH part
#else
constexpr uint32_t MOTOR_PWM_HZ = 2000;   // PLACEHOLDER: L298N is slow; tune for noise/heat
#endif
constexpr uint8_t MOTOR_PWM_BITS = 10;
// PLACEHOLDER: the Rover 5 motors are 7.2 V and the pack is 12.6 V. Until the student decides how
// the motor voltage is limited (driver drop, buck converter, duty cap), the duty cap stays 0.
constexpr float MOTOR_MAX_DUTY = 0.0f;
constexpr bool MOTOR_INVERT_LEFT = false;   // PLACEHOLDER: true if positive duty runs the track backward
constexpr bool MOTOR_INVERT_RIGHT = false;  // PLACEHOLDER

// ---- Encoders --------------------------------------------------------------------------------
constexpr uint16_t ENCODER_GLITCH_FILTER = 0;        // PLACEHOLDER: PCNT glitch filter (APB clock cycles), 0 = off
constexpr float ENCODER_FAULT_MIN_DUTY = 1.0f;       // PLACEHOLDER: duty above which a silent encoder is a fault
constexpr uint32_t ENCODER_FAULT_TIMEOUT_MS = 0;     // PLACEHOLDER: 0 = detection off

// ---- Servo (MG996R pan) ----------------------------------------------------------------------
constexpr int16_t SERVO_MIN_CDEG = 0;         // PLACEHOLDER right limit (0.01 deg); 0/0 = the servo stays centred
constexpr int16_t SERVO_MAX_CDEG = 0;         // PLACEHOLDER left limit
constexpr float SERVO_SLEW_CDEG_PER_S = 0.0f; // PLACEHOLDER slew-rate limit; 0 = no movement
constexpr float SERVO_CENTER_PULSE_US = 1500.0f;  // nominal hobby-servo centre; PLACEHOLDER until trimmed
constexpr float SERVO_US_PER_DEG = 0.0f;      // PLACEHOLDER: measure (pulse change per degree)
constexpr bool SERVO_INVERT = false;          // PLACEHOLDER: positive angle must turn the camera LEFT
constexpr uint16_t SERVO_PULSE_MIN_US = 1500; // PLACEHOLDER hard pulse limits (kept at centre)
constexpr uint16_t SERVO_PULSE_MAX_US = 1500;

// ---- IMU (ICM-20948) -------------------------------------------------------------------------
constexpr uint8_t IMU_I2C_ADDRESS = 0x68;     // PLACEHOLDER: 0x68 or 0x69, depends on the breakout's AD0
// PLACEHOLDER axis mapping: which chip axis points forward / left / up depends on how the board
// is mounted. Index 0..2 = chip x/y/z, sign +1/-1.
constexpr uint8_t IMU_AXIS_FORWARD = 0;
constexpr uint8_t IMU_AXIS_LEFT = 1;
constexpr uint8_t IMU_AXIS_UP = 2;
constexpr int8_t IMU_SIGN_FORWARD = 1;
constexpr int8_t IMU_SIGN_LEFT = 1;
constexpr int8_t IMU_SIGN_UP = 1;
constexpr uint16_t GYRO_CAL_SAMPLES = 200;            // about 2 s at 100 Hz (protocol document)
constexpr int32_t GYRO_CAL_MAX_SPREAD_URAD_S = 50000; // PLACEHOLDER: max-min of the raw rate that still counts as "still"

// ---- Power (INA219) --------------------------------------------------------------------------
constexpr uint8_t INA219_I2C_ADDRESS = 0x40;      // PLACEHOLDER: breakout default, depends on its address jumpers
constexpr float INA219_SHUNT_OHMS = 0.1f;         // PLACEHOLDER: read the value printed on the breakout's shunt
constexpr bool INA219_INVERT_CURRENT = false;     // PLACEHOLDER: positive must mean discharging
constexpr uint16_t LOW_BATTERY_MV = 0;            // PLACEHOLDER: 0 = low-battery detection OFF until the student sets it from the pack/BMS data
constexpr uint16_t LOW_BATTERY_RECOVER_MV = 0;    // PLACEHOLDER
constexpr uint32_t LOW_BATTERY_HOLD_MS = 2000;    // PLACEHOLDER
constexpr uint32_t I2C_HZ = 100000;               // conservative; raise after the wiring is checked

// ---- Ultrasonic (HC-SR04 x4) -----------------------------------------------------------------
constexpr uint32_t US_SLOT_MS = 30;               // PLACEHOLDER: one sensor per slot, 4 sensors -> one round = 4 slots
constexpr uint32_t US_ECHO_TIMEOUT_US = 25000;    // PLACEHOLDER: must be shorter than the slot
constexpr uint8_t US_ORDER[4] = {0, 2, 1, 3};     // FL, FR, FC, rear: neighbours are not fired one after the other
constexpr uint8_t US_FAULT_AFTER_MISSES = 3;      // PLACEHOLDER
constexpr uint16_t US_MAX_RANGE_MM = 4000;        // HC-SR04 nominal range; beyond = "no echo"

// ---- Builds the application configuration from the constants above ---------------------------
inline AppConfig makeAppConfig() {
  AppConfig c;
  c.control_period_ms = CONTROL_PERIOD_MS;
  c.safety.watchdog_ms = WATCHDOG_MS;
  c.safety.obstacle_mm = OBSTACLE_THRESHOLD_MM;
  c.safety.clear_hysteresis_mm = OBSTACLE_CLEAR_HYSTERESIS_MM;
  c.pid.kp = PID_KP;
  c.pid.ki = PID_KI;
  c.pid.kd = PID_KD;
  c.pid.kff = PID_KFF;
  c.pid.out_min = -1.0f;
  c.pid.out_max = 1.0f;
  c.metres_per_tick = METRES_PER_TICK;
  c.speed_filter_alpha = SPEED_FILTER_ALPHA;
  for (int i = 0; i < 4; ++i) c.encoder_invert[i] = ENCODER_INVERT[i];
  c.ramp_mm_s2 = RAMP_MM_S2;
  c.max_speed_mm_s = MAX_SPEED_MM_S;
  c.test_full_scale_mm_s = TEST_FULL_SCALE_MM_S;
  c.test_max_duty = TEST_MAX_DUTY;
  c.servo.min_cdeg = SERVO_MIN_CDEG;
  c.servo.max_cdeg = SERVO_MAX_CDEG;
  c.servo.slew_cdeg_per_s = SERVO_SLEW_CDEG_PER_S;
  c.servo.center_pulse_us = SERVO_CENTER_PULSE_US;
  c.servo.us_per_deg = SERVO_US_PER_DEG;
  c.servo.invert = SERVO_INVERT;
  c.servo.pulse_min_us = SERVO_PULSE_MIN_US;
  c.servo.pulse_max_us = SERVO_PULSE_MAX_US;
  c.gyro_cal.samples = GYRO_CAL_SAMPLES;
  c.gyro_cal.max_spread_urad_s = GYRO_CAL_MAX_SPREAD_URAD_S;
  c.battery.low_mv = LOW_BATTERY_MV;
  c.battery.recover_mv = LOW_BATTERY_RECOVER_MV;
  c.battery.hold_ms = LOW_BATTERY_HOLD_MS;
  c.encoder_fault.min_duty = ENCODER_FAULT_MIN_DUTY;
  c.encoder_fault.timeout_ms = ENCODER_FAULT_TIMEOUT_MS;
  c.ultrasonic.slot_ms = US_SLOT_MS;
  for (int i = 0; i < 4; ++i) c.ultrasonic.order[i] = US_ORDER[i];
  c.ultrasonic.fault_after_misses = US_FAULT_AFTER_MISSES;
  c.ultrasonic.max_range_mm = US_MAX_RANGE_MM;
  return c;
}

}  // namespace cfg
}  // namespace scr
