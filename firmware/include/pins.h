// Pin map of the ESP32 DevKit — THE ONLY FILE WITH PIN NUMBERS (CLAUDE.md rule 8).
//
// Every value is -1 = NOT ASSIGNED until the wiring is confirmed and written into the
// "Pin map — to be confirmed after wiring" table of docs/HARDWARE.md.
//
// Procedure (firmware/README.md, "PINS_CONFIRMED"):
//   1. Wire the robot, fill in the table in docs/HARDWARE.md.
//   2. Copy the numbers from that table into this file.
//   3. Add -DPINS_CONFIRMED to build_flags of [env:esp32dev] in platformio.ini.
// Until step 3 the real firmware does not compile. Even after it, a driver whose pin is still
// negative refuses to start at run time (scr::pins::allAssigned in src/drivers_esp32).
//
// The native tests and the sim-check build never include this file.
#pragma once

#if !defined(PINS_CONFIRMED) && !defined(PINS_COMPILE_CHECK_ONLY)
#error "Pins are not confirmed. Fill in firmware/include/pins.h from the pin-map table in docs/HARDWARE.md, then build with -DPINS_CONFIRMED (see firmware/README.md)."
#endif

#if defined(PINS_CONFIRMED) && defined(PINS_COMPILE_CHECK_ONLY)
#error "PINS_COMPILE_CHECK_ONLY is for the compile check with unassigned pins; do not combine it with PINS_CONFIRMED."
#endif

#define PIN_UNASSIGNED (-1)

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
