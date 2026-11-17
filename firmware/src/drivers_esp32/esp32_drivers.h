// Real Arduino-ESP32 drivers behind the HAL interfaces (lib/hal/scr_hal.h).
// Compiled only for the esp32 environments. NOT VERIFIED ON HARDWARE: written before the parts
// existed, never run on an ESP32. Pin numbers come only from include/pins.h; none are written here.
//
// Tasks on the ESP32 (src/main_esp32.cpp):
//   control    100 Hz, prio 5  FirmwareApp::controlTick (safety). NEVER touches I2C.
//   telemetry   50 Hz, prio 4  FirmwareApp::telemetryTick
//   i2c        100 Hz, prio 3  every I2C transfer: IMU, INA219, MCP23017 triggers, the scan,
//                               the bus recovery. The drivers below only copy its results.
#pragma once

#include <Arduino.h>

#include <initializer_list>

#include "config.h"
#include "pins.h"
#include "scr_hal.h"

// Motor direction lines on the MCP23017 are refused (docs/PIN_PLAN.md section 4: a failed I2C
// write would leave a wrong direction with the PWM on, and the expander keeps its outputs over an
// ESP32 reset). Only the HC-SR04 trigger lines may be expander pins.
#if SCR_PIN_IS_EXPANDER(PIN_MOTOR_LEFT_DIR_A) || SCR_PIN_IS_EXPANDER(PIN_MOTOR_LEFT_DIR_B) || \
    SCR_PIN_IS_EXPANDER(PIN_MOTOR_RIGHT_DIR_A) || SCR_PIN_IS_EXPANDER(PIN_MOTOR_RIGHT_DIR_B)
#error "Motor direction lines on the I2C expander are not supported by this firmware (fail-unsafe); put them on GPIO."
#endif

namespace scr {
namespace pins {

// Run-time half of CLAUDE.md rule 8: a driver starts only when every one of its pins is
// assigned. In the compile-check build and in the CI pin build nothing is ever usable, so those
// builds never drive a pin.
inline bool allAssigned(std::initializer_list<int> list) {
#if defined(PINS_COMPILE_CHECK_ONLY) || defined(SCR_CI_PINS_DO_NOT_FLASH)
  (void)list;
  return false;
#else
  for (int p : list) {
    if (p < 0) return false;
  }
  return true;
#endif
}

inline bool isExpander(int pin) { return SCR_PIN_IS_EXPANDER(pin); }
inline bool isGpio(int pin) { return pin >= 0 && pin < SCR_PIN_EXPANDER_BASE; }

}  // namespace pins

namespace esp32 {

// The very first thing setup() does (S2.4): every assigned motor GPIO (PWM and direction) as an
// output driven LOW, before the serial port, NVS or anything else. Together with the 10 kOhm
// pull-downs on ENA/ENB (docs/PIN_PLAN.md) the tracks cannot move while the ESP32 boots.
// Does nothing in the compile-check and CI builds (they never drive a pin).
void motorPinsLowAtBoot();

// LEDC channel numbers (internal PWM channels of the chip, not pins). Channels are paired on
// timers, so the motors (same frequency) and the servo (50 Hz) use different pairs.
constexpr uint8_t kLedcChannelMotorLeft = 0;
constexpr uint8_t kLedcChannelMotorRight = 1;
constexpr uint8_t kLedcChannelServo = 4;

// ---- I2C task (esp32_sensors.cpp) -----------------------------------------------------------
// Starts the shared bus once (false when its pins are not assigned). Called from setup() only.
bool i2cBusBegin();
// Starts the I2C task (after the drivers' begin()). No-op when the bus is not usable.
void i2cTaskStart();
// System services (hal::ISystem) backed by the I2C task and the control-loop statistics.
void recordControlPeriodUs(uint32_t period_us);  // called by the control task once per tick

// motors: PWM + two direction signals per side. VNH or L298N is chosen in config.h
// (SCR_MOTOR_DRIVER_VNH / SCR_MOTOR_DRIVER_L298N); only the PWM frequency differs here.
// Duty caps and inversion are applied by the application (runtime parameters); this driver
// only clamps to [-1, 1].
class Esp32Motors : public hal::IMotorDriver {
 public:
  bool begin() override;
  void setEnabled(bool enabled) override;
  void setTrackDuty(float left, float right) override;

 private:
  void writeSide(int pwm_pin, uint8_t channel, int dir_a, int dir_b, float duty);
  bool ok_ = false;
  bool enabled_ = false;
};

// encoders: PCNT, full quadrature, one unit per encoder. Core 2.x: legacy driver/pcnt.h;
// core 3.x (ESP-IDF 5): driver/pulse_cnt.h. Both count 16 bit and reset at +-limit; the wrap is
// undone by scr::pcntDelta (unit-tested natively at both ends).
class Esp32Encoders : public hal::IEncoders {
 public:
  bool begin() override;
  void read(int32_t counts[4]) override;

 private:
  bool ok_ = false;
  int16_t last_[4] = {0, 0, 0, 0};
  int32_t total_[4] = {0, 0, 0, 0};
};

// imu: ICM-20948 over I2C. begin() configures it (from setup()); read() copies the I2C task's
// latest sample and is false when it is older than 50 ms.
class Esp32Imu : public hal::IImu {
 public:
  bool begin() override;
  bool read(hal::ImuSample* out) override;
};

// ultrasonic: HC-SR04 x4, echo timed by pin-change interrupts (non-blocking). A trigger line on
// the MCP23017 is pulsed by the I2C task (the request returns at once).
class Esp32Ultrasonic : public hal::IUltrasonic {
 public:
  bool begin() override;
  bool present(uint8_t index) const override;
  void trigger(uint8_t index) override;
  hal::EchoState collect(uint8_t index, uint32_t* echo_us) override;

 private:
  bool present_[4] = {false, false, false, false};
};

// servo: LEDC, 50 Hz.
class Esp32Servo : public hal::IServo {
 public:
  bool begin() override;
  void writePulseUs(uint16_t pulse_us) override;

 private:
  bool ok_ = false;
};

// power: INA219 over I2C, raw values copied from the I2C task (fresh = younger than 500 ms).
class Esp32Power : public hal::IPowerSensor {
 public:
  bool begin() override;
  bool read(uint16_t* bus_mv, int32_t* shunt_uv) override;
};

// link: the USB-UART bridge of the DevKit (the board's default serial port), 460800 8N1.
class Esp32Link : public hal::ILinkPort {
 public:
  void begin();
  size_t read(uint8_t* buf, size_t cap) override;
  void write(const uint8_t* data, size_t len) override;
};

// parameters: NVS through Arduino Preferences, namespace "scr-params", key "p<id>" (float).
class Esp32Storage : public hal::IParamStorage {
 public:
  bool begin() override;
  bool read(uint16_t id, float* value) override;
  bool write(uint16_t id, float value) override;
  bool commit() override;

 private:
  bool ok_ = false;
};

class Esp32System : public hal::ISystem {
 public:
  uint8_t resetReason() override;
  uint32_t freeHeap() override;
  void loopStats(uint16_t* period_max_us, uint16_t* jitter_us) override;
  void requestI2cScan() override;
  bool i2cScanDone() override;
  uint8_t i2cScanResult(uint8_t* addr, uint8_t cap) override;
  uint8_t takeI2cRecovered() override;
  bool expanderPresent() override;
};

}  // namespace esp32
}  // namespace scr
