// Compile-time configuration of the firmware.
//
// RUNTIME PARAMETERS (sprint 5, S2.1): every tunable that the student calibrates on the robot
// (METRES_PER_TICK, PID gains, ramp, maximum speed, duty caps, motor voltage compensation,
// motor/encoder inversions, servo limits, obstacle thresholds, battery thresholds and cut-off,
// INA219 shunt and scales, gyro scale, slip and encoder-fault thresholds) is NO LONGER here.
// Their defaults and hard min/max bounds live in shared/scr_serial/params.json (the single
// source), compiled in through the generated lib/params/scr_param_table.h, and they are changed
// at run time with PARAM_SET / PARAM_SAVE (NVS) - see firmware/README.md "Parameters".
// The defaults are still the placeholders that make an uncalibrated robot NOT move
// (METRES_PER_TICK = 0, gains 0, MOTOR_MAX_DUTY = 0, TEST_MAX_DUTY = 0, ...).
//
// What stays here: things fixed by the protocol, hardware constants that need a rebuild anyway
// (PWM frequency, I2C addresses, IMU axis mapping, ultrasonic timing).
// !!!  EVERY VALUE MARKED "PLACEHOLDER" IS A PLACEHOLDER UNTIL MEASURED  !!!
//
// No pin numbers here: pins reach the firmware only through include/pins_generated.h
// (CLAUDE.md rule 8, docs/SPRINT_5.md S1).
#pragma once

#include "scr_app.h"
#include "scr_param_table.h"
#include "scr_params.h"

// ---- Motor driver type: compile-time choice (sprint 5 default: L298N, docs/HARDWARE.md) ------
// Both take one PWM and two direction signals per motor, so only the profile below differs.
#if !defined(SCR_MOTOR_DRIVER_VNH) && !defined(SCR_MOTOR_DRIVER_L298N)
#define SCR_MOTOR_DRIVER_L298N 1
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

// ---- Robustness (S2.4) -----------------------------------------------------------------------
// Task watchdog (esp_task_wdt) on the control and telemetry tasks: a task that does not check in
// for this long resets the chip, and every boot starts in SAFE. Short on purpose: while the
// control task hangs, nothing else updates the motor outputs.
constexpr uint32_t TASK_WDT_TIMEOUT_MS = 500;
constexpr uint32_t I2C_TASK_PERIOD_MS = 10;      // IMU every period, INA219 every 10th
constexpr uint8_t I2C_FAILS_BEFORE_RECOVERY = 3; // consecutive failed transfers -> bus clear
constexpr uint16_t I2C_TIMEOUT_MS = 5;           // Wire timeout of one transfer
constexpr uint8_t I2C_SCAN_FIRST = 0x08;         // scan range (7-bit, reserved addresses skipped)
constexpr uint8_t I2C_SCAN_LAST = 0x77;

// ---- Speed-sign mismatch fault (MODE DRIVE only; safety review 5b) --------------------------
// PLACEHOLDERS to check on the robot: |duty| above SIGN_FAULT_MIN_DUTY while the measured track
// speed has the opposite sign and is above SIGN_FAULT_MIN_SPEED_MM_S for SIGN_FAULT_MS
// -> STOP, MODE SAFE, EVENT ENCODER_FAULT arg 10 (left) / 11 (right).
constexpr float SIGN_FAULT_MIN_DUTY = 0.2f;
constexpr float SIGN_FAULT_MIN_SPEED_MM_S = 50.0f;
constexpr uint32_t SIGN_FAULT_MS = 300;

// ---- Open-loop TEST mode ---------------------------------------------------------------------
constexpr float TEST_FULL_SCALE_MM_S = 1000.0f;  // a DRIVE of this many mm/s means duty 1.0 (a convention, not a measurement)

// ---- Motor PWM (depends on the driver type) --------------------------------------------------
#if defined(SCR_MOTOR_DRIVER_VNH)
constexpr uint32_t MOTOR_PWM_HZ = 16000;  // PLACEHOLDER: check the limit of the exact VNH part
constexpr uint8_t DRIVER_TYPE = 1;
#else
constexpr uint32_t MOTOR_PWM_HZ = 2000;   // PLACEHOLDER: L298N is slow; tune for noise/heat
constexpr uint8_t DRIVER_TYPE = 0;
#endif
constexpr uint8_t MOTOR_PWM_BITS = 10;

// ---- Encoders --------------------------------------------------------------------------------
constexpr uint16_t ENCODER_GLITCH_FILTER = 0;        // PLACEHOLDER: PCNT glitch filter (APB clock cycles), 0 = off

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
constexpr uint32_t LOW_BATTERY_HOLD_MS = 2000;    // PLACEHOLDER
constexpr uint32_t I2C_HZ = 100000;               // conservative; raise after the wiring is checked

// ---- I2C expander (pin plan option B: MCP23017 carries the HC-SR04 trigger lines) -----------
#if defined(SCR_EXPANDER_I2C_ADDR)
constexpr uint8_t EXPANDER_I2C_ADDRESS = SCR_EXPANDER_I2C_ADDR;  // from pins_generated.h / pins_ci.h
#else
constexpr uint8_t EXPANDER_I2C_ADDRESS = 0x20;  // MCP23017 with A0..A2 to GND (only used with expander pins)
#endif

// ---- Ultrasonic (HC-SR04 x4) -----------------------------------------------------------------
constexpr uint32_t US_SLOT_MS = 30;               // PLACEHOLDER: one sensor per slot, 4 sensors -> one round = 4 slots
constexpr uint32_t US_ECHO_TIMEOUT_US = 25000;    // PLACEHOLDER: must be shorter than the slot
constexpr uint8_t US_ORDER[4] = {0, 2, 1, 3};     // FL, FR, FC, rear: neighbours are not fired one after the other
constexpr uint8_t US_FAULT_AFTER_MISSES = 3;      // PLACEHOLDER
constexpr uint16_t US_MAX_RANGE_MM = 4000;        // HC-SR04 nominal range; beyond = "no echo"

// ---- Builds the application configuration ----------------------------------------------------
// Compile-time values from above; every runtime parameter from the generated table's defaults.
inline AppConfig makeAppConfig() {
  AppConfig c;
  c.control_period_ms = CONTROL_PERIOD_MS;
  c.safety.watchdog_ms = WATCHDOG_MS;
  c.pid.out_min = -1.0f;
  c.pid.out_max = 1.0f;
  c.test_full_scale_mm_s = TEST_FULL_SCALE_MM_S;
  c.gyro_cal.samples = GYRO_CAL_SAMPLES;
  c.gyro_cal.max_spread_urad_s = GYRO_CAL_MAX_SPREAD_URAD_S;
  c.battery.hold_ms = LOW_BATTERY_HOLD_MS;
  c.ultrasonic.slot_ms = US_SLOT_MS;
  for (int i = 0; i < 4; ++i) c.ultrasonic.order[i] = US_ORDER[i];
  c.ultrasonic.fault_after_misses = US_FAULT_AFTER_MISSES;
  c.ultrasonic.max_range_mm = US_MAX_RANGE_MM;
  c.driver_type = DRIVER_TYPE;
  c.sign_fault_min_duty = SIGN_FAULT_MIN_DUTY;
  c.sign_fault_min_speed_m_s = SIGN_FAULT_MIN_SPEED_MM_S / 1000.0f;
  c.sign_fault_ms = SIGN_FAULT_MS;
  const ParamStore table_defaults;  // every parameter at its params.json default
  paramsToConfig(table_defaults, &c);
  return c;
}

}  // namespace cfg
}  // namespace scr
