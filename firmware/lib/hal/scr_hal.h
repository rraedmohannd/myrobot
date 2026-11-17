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
  int32_t gyro_z_urad_s;  // raw: bias NOT removed, counter-clockwise positive (= gyro_urad_s[2])
  int16_t accel_mg[3];    // x forward, y left, z up
  int32_t gyro_urad_s[3]; // raw rates about forward / left / up (bring-up stream IMU_RAW)
};

// On the ESP32 the I2C transfers run in their own task (never on the control task):
// read() only copies the latest sample and returns false when it is stale or failed.
class IImu {
 public:
  virtual ~IImu() = default;
  virtual bool begin() = 0;
  virtual bool read(ImuSample* out) = 0;  // false = read error / no fresh sample
};

enum class EchoState : uint8_t {
  NoRise,         // the echo line never went high: sensor absent or faulty (a miss)
  Timeout,        // the echo started early enough but did not end in time: nothing within range
  Done,           // echo_us is valid
  Late,           // the echo is still high but rose less than the echo timeout ago: the ping
                  // went out late (e.g. delayed I2C trigger). A MISS, never "no echo".
  TriggerFailed,  // the trigger pulse could not be sent (expander write failed): ABSENT at once
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

// INA219, raw values: the application applies the runtime parameters INA219_SHUNT_OHMS,
// BATTERY_VOLTAGE_SCALE, BATTERY_CURRENT_SCALE and INA219_INVERT_CURRENT.
class IPowerSensor {
 public:
  virtual ~IPowerSensor() = default;
  virtual bool begin() = 0;
  virtual bool read(uint16_t* bus_mv, int32_t* shunt_uv) = 0;  // false = no fresh reading
};

// Persistent parameter storage (NVS "Preferences" on the ESP32, in-memory stub off-target).
class IParamStorage {
 public:
  virtual ~IParamStorage() = default;
  virtual bool begin() = 0;
  virtual bool read(uint16_t id, float* value) = 0;  // false = nothing stored for this id
  virtual bool write(uint16_t id, float value) = 0;
  virtual bool commit() = 0;
};

// System services for diagnostics and robustness. Every call is non-blocking; anything slow
// (the I2C scan, the bus recovery) happens in another task on the ESP32.
class ISystem {
 public:
  virtual ~ISystem() = default;
  virtual uint8_t resetReason() = 0;  // proto::ResetReason
  virtual uint32_t freeHeap() = 0;
  // Control-loop period statistics measured by the ESP32 main (us): max and jitter (max - min)
  // since the last call; they restart after each call.
  virtual void loopStats(uint16_t* period_max_us, uint16_t* jitter_us) = 0;
  // Ask for a new I2C scan (done in the I2C task, bounded in time) ...
  virtual void requestI2cScan() = 0;
  // ... true once it is finished; addresses of the last finished scan (7 bit, at most cap).
  virtual bool i2cScanDone() = 0;
  virtual uint8_t i2cScanResult(uint8_t* addr, uint8_t cap) = 0;
  // Number of bus-clear attempts of the last I2C bus recovery, once per recovery (0 = none).
  virtual uint8_t takeI2cRecovered() = 0;
  virtual bool expanderPresent() = 0;  // MCP23017 of pin-plan option B answered
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
  IParamStorage* storage = nullptr;  // optional: without it, parameters are not persistent
  ISystem* system = nullptr;         // optional: without it, DIAG reports zeros for these fields
};

}  // namespace hal
}  // namespace scr
