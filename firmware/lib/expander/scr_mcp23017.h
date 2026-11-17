// MCP23017 I2C expander logic for pin-plan option B (docs/PIN_PLAN.md section 4): the four HC-SR04
// trigger lines are expander pins X0..X15 (GPA0..7 = X0..7, GPB0..7 = X8..15). Hardware
// independent: register writes go through a small interface, so the sequence is unit-tested
// natively with a stub; on the ESP32 the I2C task supplies a Wire-backed writer.
// Only trigger lines are allowed on the expander (a lost bus = no ping = "absent" = driving
// blocked, fail safe). Motor direction lines on it are refused at compile time.
#pragma once

#include <cstdint>

namespace scr {
namespace mcp23017 {

constexpr uint8_t kRegIodirA = 0x00;  // IOCON.BANK = 0 (power-on default)
constexpr uint8_t kRegIodirB = 0x01;
constexpr uint8_t kRegOlatA = 0x14;
constexpr uint8_t kRegOlatB = 0x15;

class IRegWriter {
 public:
  virtual ~IRegWriter() = default;
  virtual bool writeReg(uint8_t address, uint8_t reg, uint8_t value) = 0;
  virtual void waitUs(uint32_t us) = 0;
};

class Expander {
 public:
  explicit Expander(uint8_t address) : address_(address) {}

  // Outputs LOW first (both output latches 0), THEN the pins in output_mask become outputs; every
  // other pin stays an input. False (and not usable) when any write fails or the mask is empty.
  bool begin(IRegWriter* w, uint16_t output_mask);
  // One trigger pulse on pin `bit` (0..15, must be an output): latch high, >= 10 us, latch low.
  // The low write is attempted even when the high write failed.
  bool pulse(IRegWriter* w, uint8_t bit);
  bool ok() const { return ok_; }
  uint16_t outputMask() const { return mask_; }

 private:
  uint8_t address_;
  uint16_t mask_ = 0;
  bool ok_ = false;
};

}  // namespace mcp23017
}  // namespace scr
