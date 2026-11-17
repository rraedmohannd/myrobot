// Real Arduino-ESP32 drivers behind the HAL interfaces (lib/hal/scr_hal.h).
// Compiled only for the esp32 environments. NOT VERIFIED ON HARDWARE: written before the parts
// existed. Pin numbers come only from include/pins.h; none are written here.
#pragma once

#include <Arduino.h>

#include <initializer_list>

#include "config.h"
#include "pins.h"
#include "scr_hal.h"

namespace scr {
namespace pins {

// Run-time half of CLAUDE.md rule 8: a driver starts only when every one of its pins is
// assigned. In the compile-check build nothing is ever usable.
inline bool allAssigned(std::initializer_list<int> list) {
#if defined(PINS_COMPILE_CHECK_ONLY)
  (void)list;
  return false;
#else
  for (int p : list) {
    if (p < 0) return false;
  }
  return true;
#endif
}

}  // namespace pins

namespace esp32 {

// LEDC channel numbers (internal PWM channels of the chip, not pins). Channels are paired on
// timers, so the motors (same frequency) and the servo (50 Hz) use different pairs.
constexpr uint8_t kLedcChannelMotorLeft = 0;
constexpr uint8_t kLedcChannelMotorRight = 1;
constexpr uint8_t kLedcChannelServo = 4;

// Starts the shared I2C bus once. False when its pins are not assigned.
bool i2cBusBegin();
bool i2cWriteReg(uint8_t address, uint8_t reg, uint8_t value);
bool i2cReadRegs(uint8_t address, uint8_t reg, uint8_t* out, size_t len);

// motors: PWM + two direction signals per side. VNH or L298N is chosen in config.h
// (SCR_MOTOR_DRIVER_VNH / SCR_MOTOR_DRIVER_L298N); only the PWM frequency differs here.
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

// encoders: PCNT, full quadrature, one unit per encoder.
class Esp32Encoders : public hal::IEncoders {
 public:
  bool begin() override;
  void read(int32_t counts[4]) override;

 private:
  bool ok_ = false;
  int16_t last_[4] = {0, 0, 0, 0};
  int32_t total_[4] = {0, 0, 0, 0};
};

// imu: ICM-20948 over I2C, gyro z + accelerometer.
class Esp32Imu : public hal::IImu {
 public:
  bool begin() override;
  bool read(hal::ImuSample* out) override;

 private:
  bool ok_ = false;
};

// ultrasonic: HC-SR04 x4, echo timed by pin-change interrupts (non-blocking).
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

// power: INA219 over I2C.
class Esp32Power : public hal::IPowerSensor {
 public:
  bool begin() override;
  bool read(uint16_t* bus_mv, int16_t* current_ma) override;

 private:
  bool ok_ = false;
};

// link: the USB-UART bridge of the DevKit (the board's default serial port), 460800 8N1.
class Esp32Link : public hal::ILinkPort {
 public:
  void begin();
  size_t read(uint8_t* buf, size_t cap) override;
  void write(const uint8_t* data, size_t len) override;
};

}  // namespace esp32
}  // namespace scr
