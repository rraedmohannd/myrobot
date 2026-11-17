#include "scr_mcp23017.h"

namespace scr {
namespace mcp23017 {

bool Expander::begin(IRegWriter* w, uint16_t output_mask) {
  ok_ = false;
  mask_ = output_mask;
  if (w == nullptr || output_mask == 0) return false;
  // 1. latches low, so that a pin is LOW from the moment it becomes an output
  if (!w->writeReg(address_, kRegOlatA, 0x00)) return false;
  if (!w->writeReg(address_, kRegOlatB, 0x00)) return false;
  // 2. direction: 0 = output (only the trigger pins), 1 = input (everything else)
  const uint16_t dir = static_cast<uint16_t>(~output_mask);
  if (!w->writeReg(address_, kRegIodirA, static_cast<uint8_t>(dir & 0xFF))) return false;
  if (!w->writeReg(address_, kRegIodirB, static_cast<uint8_t>(dir >> 8))) return false;
  ok_ = true;
  return true;
}

bool Expander::pulse(IRegWriter* w, uint8_t bit) {
  if (!ok_ || w == nullptr || bit > 15 || !(mask_ & (1u << bit))) return false;
  const uint8_t reg = bit < 8 ? kRegOlatA : kRegOlatB;
  const uint8_t high = static_cast<uint8_t>(1u << (bit & 7));  // all other outputs stay LOW
  const bool a = w->writeReg(address_, reg, high);
  w->waitUs(10);  // HC-SR04: >= 10 us (one I2C write alone already takes ~75 us at 400 kHz)
  const bool b = w->writeReg(address_, reg, 0x00);
  return a && b;
}

}  // namespace mcp23017
}  // namespace scr
