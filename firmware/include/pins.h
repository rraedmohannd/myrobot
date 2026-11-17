// Pin map of the ESP32 DevKit (CLAUDE.md rule 8). No pin number is ever written in this file.
//
// Pin numbers reach the firmware ONLY this way (docs/SPRINT_5.md S1.2):
//   docs/HARDWARE.md pin-map table  --firmware/tools/pins_from_table.py-->  pins_generated.h
// The tool checks the table (duplicates, flash/UART0/input-only pins, strapping pins without a
// "yes", missing signals, missing "Checked by / date") and refuses to write on any error.
// pins_generated.h is the ONLY file that defines PINS_CONFIRMED. After wiring, the student
// commits it together with the filled-in table: it is the record of the pins the firmware uses.
// docs/PIN_PLAN.md is a PROPOSAL on paper; the firmware does not use it.
//
// Exactly one of three mutually exclusive paths is taken:
//
//   1. SCR_CI_PINS_DO_NOT_FLASH defined (env esp32-ci-pins): includes pins_ci.h, a fake but valid
//      pin set FOR COMPILING ONLY (written with the firmware, not from the table). That build
//      boots into permanent SAFE mode and never drives a pin. pins_ci.h must NOT define
//      PINS_CONFIRMED. Combining it with PINS_COMPILE_CHECK_ONLY or PINS_CONFIRMED is an #error;
//      pins_generated.h is ignored on this path.
//   2. PINS_COMPILE_CHECK_ONLY defined (env esp32-compile-check): every pin is -1, every driver
//      refuses to start. pins_generated.h is ignored on this path.
//   3. Otherwise (env esp32dev, the real firmware): includes pins_generated.h when it exists.
//      Without it, the build stops with the "Pins are not confirmed" #error. PINS_CONFIRMED given
//      any other way (e.g. -DPINS_CONFIRMED in platformio.ini) is an #error: the table and the
//      checker cannot be bypassed.
//
// Even with confirmed pins, a driver whose pin is still negative refuses to start at run time
// (scr::pins::allAssigned in src/drivers_esp32). The native tests and the sim-check build never
// include this file.
#pragma once

#define PIN_UNASSIGNED (-1)

// Option B of docs/PIN_PLAN.md: a value SCR_PIN_EXPANDER_BASE + n (n = 0..15) means pin Xn of the
// MCP23017 expander on the I2C bus, not a GPIO. Must match EXPANDER_BASE in pins_from_table.py.
#define SCR_PIN_EXPANDER_BASE 100
#define SCR_PIN_IS_EXPANDER(p) ((p) >= SCR_PIN_EXPANDER_BASE && (p) < SCR_PIN_EXPANDER_BASE + 16)
#define SCR_PIN_EXPANDER_INDEX(p) ((p) - SCR_PIN_EXPANDER_BASE)

#if defined(SCR_CI_PINS_DO_NOT_FLASH)
// ---- path 1: compile-only CI pin set ---------------------------------------------------------
#if defined(PINS_CONFIRMED)
#error "SCR_CI_PINS_DO_NOT_FLASH (CI compile-only pins) must not be combined with PINS_CONFIRMED."
#endif
#if defined(PINS_COMPILE_CHECK_ONLY)
#error "SCR_CI_PINS_DO_NOT_FLASH and PINS_COMPILE_CHECK_ONLY are two different builds; pick one."
#endif
#if !__has_include("pins_ci.h")
#error "SCR_CI_PINS_DO_NOT_FLASH is defined but include/pins_ci.h does not exist."
#else
#include "pins_ci.h"
#endif
#if defined(PINS_CONFIRMED)
#error "pins_ci.h must not define PINS_CONFIRMED (only pins_generated.h may)."
#endif
#define SCR_PINS_SOURCE_CI 1

#elif defined(PINS_COMPILE_CHECK_ONLY)
// ---- path 2: compile check with unassigned pins ----------------------------------------------
#if defined(PINS_CONFIRMED)
#error "PINS_COMPILE_CHECK_ONLY is for the compile check with unassigned pins; do not combine it with PINS_CONFIRMED."
#endif
#define SCR_PINS_USE_UNASSIGNED 1

#else
// ---- path 3: the real firmware, pins from the checked table ----------------------------------
#if defined(PINS_CONFIRMED)
#error "PINS_CONFIRMED must come only from pins_generated.h (run firmware/tools/pins_from_table.py); remove -DPINS_CONFIRMED from the build flags."
#endif
#if __has_include("pins_generated.h")
#define SCR_PINS_H_INCLUDING_GENERATED 1
#include "pins_generated.h"
#undef SCR_PINS_H_INCLUDING_GENERATED
#if !defined(PINS_CONFIRMED) || !defined(PINS_GENERATED_BY_TOOL)
#error "pins_generated.h was not written by firmware/tools/pins_from_table.py; regenerate it."
#endif
#if defined(SCR_PINS_USE_EXPANDER) && !defined(SCR_FIRMWARE_HAS_PIN_EXPANDER)
#error "The pin-map table uses MCP23017 expander pins (X0..X15) but this firmware has no expander driver (SCR_FIRMWARE_HAS_PIN_EXPANDER)."
#endif
#define SCR_PINS_SOURCE_GENERATED 1
#else
#error "Pins are not confirmed. Fill in the pin-map table in docs/HARDWARE.md after wiring, then run python firmware/tools/pins_from_table.py to write include/pins_generated.h (see firmware/README.md)."
#define SCR_PINS_USE_UNASSIGNED 1
#endif
#endif

#if defined(SCR_PINS_USE_UNASSIGNED)
// The signal list. pins_from_table.py reads the PIN_* names from these lines: every one of them
// must have a row in the table.

// Motor drivers: one PWM and two direction signals per SIDE (both motors of a side are driven
// together: the front and the rear driver channel of a side share these three signals).
#define PIN_MOTOR_LEFT_PWM    PIN_UNASSIGNED
#define PIN_MOTOR_LEFT_DIR_A  PIN_UNASSIGNED
#define PIN_MOTOR_LEFT_DIR_B  PIN_UNASSIGNED
#define PIN_MOTOR_RIGHT_PWM   PIN_UNASSIGNED
#define PIN_MOTOR_RIGHT_DIR_A PIN_UNASSIGNED
#define PIN_MOTOR_RIGHT_DIR_B PIN_UNASSIGNED

// Quadrature encoders (through the 5 V -> 3.3 V level shifters): front-left, rear-left,
// front-right, rear-right.
#define PIN_ENC_FL_A PIN_UNASSIGNED
#define PIN_ENC_FL_B PIN_UNASSIGNED
#define PIN_ENC_RL_A PIN_UNASSIGNED
#define PIN_ENC_RL_B PIN_UNASSIGNED
#define PIN_ENC_FR_A PIN_UNASSIGNED
#define PIN_ENC_FR_B PIN_UNASSIGNED
#define PIN_ENC_RR_A PIN_UNASSIGNED
#define PIN_ENC_RR_B PIN_UNASSIGNED

// I2C bus shared by the ICM-20948 and the INA219.
#define PIN_I2C_SDA PIN_UNASSIGNED
#define PIN_I2C_SCL PIN_UNASSIGNED

// HC-SR04 (echo is 5 V: through a level shifter): front-left, front-centre, front-right, rear.
#define PIN_US_FL_TRIG   PIN_UNASSIGNED
#define PIN_US_FL_ECHO   PIN_UNASSIGNED
#define PIN_US_FC_TRIG   PIN_UNASSIGNED
#define PIN_US_FC_ECHO   PIN_UNASSIGNED
#define PIN_US_FR_TRIG   PIN_UNASSIGNED
#define PIN_US_FR_ECHO   PIN_UNASSIGNED
#define PIN_US_REAR_TRIG PIN_UNASSIGNED
#define PIN_US_REAR_ECHO PIN_UNASSIGNED

// MG996R pan servo signal.
#define PIN_SERVO_PAN PIN_UNASSIGNED
#endif

// Whatever the path, every signal must now be defined.
#if !defined(PIN_MOTOR_LEFT_PWM) || !defined(PIN_MOTOR_LEFT_DIR_A) || !defined(PIN_MOTOR_LEFT_DIR_B) || \
    !defined(PIN_MOTOR_RIGHT_PWM) || !defined(PIN_MOTOR_RIGHT_DIR_A) || !defined(PIN_MOTOR_RIGHT_DIR_B) || \
    !defined(PIN_ENC_FL_A) || !defined(PIN_ENC_FL_B) || !defined(PIN_ENC_RL_A) || !defined(PIN_ENC_RL_B) || \
    !defined(PIN_ENC_FR_A) || !defined(PIN_ENC_FR_B) || !defined(PIN_ENC_RR_A) || !defined(PIN_ENC_RR_B) || \
    !defined(PIN_I2C_SDA) || !defined(PIN_I2C_SCL) || \
    !defined(PIN_US_FL_TRIG) || !defined(PIN_US_FL_ECHO) || !defined(PIN_US_FC_TRIG) || !defined(PIN_US_FC_ECHO) || \
    !defined(PIN_US_FR_TRIG) || !defined(PIN_US_FR_ECHO) || !defined(PIN_US_REAR_TRIG) || !defined(PIN_US_REAR_ECHO) || \
    !defined(PIN_SERVO_PAN)
#error "A PIN_* constant is missing from the selected pin source (pins_generated.h or pins_ci.h)."
#endif
