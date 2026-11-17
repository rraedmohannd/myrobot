// =============================================================================================
//  FAKE — COMPILE ONLY — NEVER FLASH
// =============================================================================================
// Pin set for the CI compile check (envs esp32-ci-pins and esp32-ci-pins-core3, build flag
// SCR_CI_PINS_DO_NOT_FLASH). It exists so that the real driver code is compiled with real pin
// numbers on Arduino-ESP32 core 2.x and 3.x. It is NOT the robot's wiring and was NOT written
// from docs/HARDWARE.md (CLAUDE.md rule 8): the real pins come only from pins_generated.h.
//
// A firmware built with this file boots into permanent SAFE mode, prints
// "CI build — not flashable" once, never starts a driver and never drives a pin
// (scr::pins::allAssigned() is false in this build; see src/main_esp32.cpp).
//
// The numbers are nevertheless VALID for an ESP32-WROOM-32 DevKit, so the compile check means
// something: no flash pins (6-11), no UART0 (1, 3), no GPIO0/2/12, input-only pins 34/35/36/39
// used only as inputs, GPIO5/15 (harmless strapping pins) used only as inputs. It follows option
// B of docs/PIN_PLAN.md: the four HC-SR04 trigger lines are on the MCP23017 expander (X0..X3),
// so the expander code path is compiled too.
//
// This file must define every PIN_* of pins.h and must NOT define PINS_CONFIRMED.
#pragma once

#if !defined(SCR_CI_PINS_DO_NOT_FLASH)
#error "pins_ci.h is only for SCR_CI_PINS_DO_NOT_FLASH builds (compile only, never flash)."
#endif

// Motors (outputs)
#define PIN_MOTOR_LEFT_PWM    25
#define PIN_MOTOR_LEFT_DIR_A  26
#define PIN_MOTOR_LEFT_DIR_B  27
#define PIN_MOTOR_RIGHT_PWM   32
#define PIN_MOTOR_RIGHT_DIR_A 33
#define PIN_MOTOR_RIGHT_DIR_B 4

// Encoders (inputs; the input-only pins are used here)
#define PIN_ENC_FL_A 34
#define PIN_ENC_FL_B 35
#define PIN_ENC_RL_A 36
#define PIN_ENC_RL_B 39
#define PIN_ENC_FR_A 13
#define PIN_ENC_FR_B 14
#define PIN_ENC_RR_A 16
#define PIN_ENC_RR_B 17

// I2C (ICM-20948, INA219, MCP23017)
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22

// HC-SR04: triggers on the MCP23017 (X0..X3), echoes on GPIO inputs
#define PIN_US_FL_TRIG   (SCR_PIN_EXPANDER_BASE + 0)
#define PIN_US_FL_ECHO   18
#define PIN_US_FC_TRIG   (SCR_PIN_EXPANDER_BASE + 1)
#define PIN_US_FC_ECHO   19
#define PIN_US_FR_TRIG   (SCR_PIN_EXPANDER_BASE + 2)
#define PIN_US_FR_ECHO   5
#define PIN_US_REAR_TRIG (SCR_PIN_EXPANDER_BASE + 3)
#define PIN_US_REAR_ECHO 15

// Servo (output)
#define PIN_SERVO_PAN 23

#define SCR_PINS_USE_EXPANDER 1
#define SCR_EXPANDER_I2C_ADDR 0x20
