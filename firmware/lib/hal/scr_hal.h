// Hardware abstraction: thin driver interfaces. Two implementations exist:
//   - src/drivers_esp32/  real Arduino-ESP32 drivers (compiled only for the esp32 environments)
//   - lib/drivers_stub/   stub drivers for the native tests and the sim-check build
// No Arduino headers and no pin numbers here.
#pragma once

#include <cstddef>
#include <cstdint>

namespace scr {
namespace hal {

// Every driver: begin() returns false when it must not be used (pin not assigned, device not
// found). The application then never enables it (run-time part of CLAUDE.md rule 8).

// Motor driver for the two tracks. Both motors of one side are driven together.
// The concrete driver (VNH or L298N) is a compile-time choice inside the implementation:
// both take one PWM and two direction signals per side.
class IMotorDriver {
 public:
  virtual ~IMotorDriver() = default;
  virtual bool begin() = 0;
  virtual void setEnabled(bool enabled) = 0;                // false = outputs off, PWM zero
  virtual void setTrackDuty(float left, float right) = 0;   // -1..1, positive = forward
};

// Four quadrature encoders: front-left, rear-left, front-right, rear-right.
class IEncoders {
 public:
  virtual ~IEncoders() = default;
  virtual bool begin() = 0;
  virtual void read(int32_t counts[4]) = 0;  // cumulative, forward = counting up
};

struct ImuSample {
  int32_t gyro_z_urad_s;  // raw: bias NOT removed, counter-clockwise positive
  int16_t accel_mg[3];    // x forward, y left, z up
};

class IImu {
 public:
  virtual ~IImu() = default;
  virtual bool begin() = 0;
  virtual bool read(ImuSample* out) = 0;  // false = read error
};

enum class EchoState : uint8_t {
  NoRise,   // the echo line never went high: sensor absent or faulty
  Timeout,  // the echo started but did not end in time: nothing within range
  Done,     // echo_us is valid
};

// Four HC-SR04: front-left, front-centre, front-right, rear.
class IUltrasonic {
 public:
  virtual ~IUltrasonic() = default;
  virtual bool begin() = 0;
  virtual bool present(uint8_t index) const = 0;  // false = not wired / pin not assigned
  virtual void trigger(uint8_t index) = 0;        // start one measurement (non-blocking)
  virtual EchoState collect(uint8_t index, uint32_t* echo_us) = 0;  // result of the last trigger
};

class IServo {
 public:
  virtual ~IServo() = default;
  virtual bool begin() = 0;
  virtual void writePulseUs(uint16_t pulse_us) = 0;
};

class IPowerSensor {
 public:
  virtual ~IPowerSensor() = default;
  virtual bool begin() = 0;
  virtual bool read(uint16_t* bus_mv, int16_t* current_ma) = 0;  // positive = discharging
};

// The serial port to the Pi. Binary protocol frames only.
class ILinkPort {
 public:
  virtual ~ILinkPort() = default;
  virtual size_t read(uint8_t* buf, size_t cap) = 0;  // non-blocking; 0 = nothing waiting
  virtual void write(const uint8_t* data, size_t len) = 0;
};

struct Hardware {
  IMotorDriver* motors = nullptr;
  IEncoders* encoders = nullptr;
  IImu* imu = nullptr;
  IUltrasonic* ultrasonic = nullptr;
  IServo* servo = nullptr;
  IPowerSensor* power = nullptr;
  ILinkPort* link = nullptr;
};

}  // namespace hal
}  // namespace scr
