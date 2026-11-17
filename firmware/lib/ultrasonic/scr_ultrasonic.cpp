#include "scr_ultrasonic.h"

namespace scr {

UltrasonicScheduler::UltrasonicScheduler(const UltrasonicConfig& c, hal::IUltrasonic* hw)
    : c_(c), hw_(hw) {
  for (int i = 0; i < 4; ++i) {
    mm_[i] = proto::kUsAbsent;
    misses_[i] = 0;
  }
}

void UltrasonicScheduler::begin(bool hw_ok) {
  hw_ok_ = hw_ok && hw_ != nullptr;
  active_ = -1;
  order_pos_ = 0;
  for (int i = 0; i < 4; ++i) {
    mm_[i] = proto::kUsAbsent;
    misses_[i] = 0;
  }
}

int8_t UltrasonicScheduler::nextPresent() {
  for (int tries = 0; tries < 4; ++tries) {
    const uint8_t idx = static_cast<uint8_t>(c_.order[order_pos_] & 3);
    order_pos_ = static_cast<uint8_t>((order_pos_ + 1) % 4);
    if (order_pos_ == 0) ++rounds_;
    if (hw_->present(idx)) return static_cast<int8_t>(idx);
    mm_[idx] = proto::kUsAbsent;
  }
  return -1;
}

void UltrasonicScheduler::tick(uint32_t now_ms) {
  if (!hw_ok_) return;
  if (active_ >= 0) {
    if (now_ms - trigger_ms_ < c_.slot_ms) return;  // the slot of the active sensor is still open
    const uint8_t i = static_cast<uint8_t>(active_);
    uint32_t echo_us = 0;
    switch (hw_->collect(i, &echo_us)) {
      case hal::EchoState::Done: {
        const float mm = static_cast<float>(echo_us) * c_.mm_per_us;
        mm_[i] = mm > static_cast<float>(c_.max_range_mm) ? proto::kUsNoEcho
                                                          : static_cast<uint16_t>(mm + 0.5f);
        misses_[i] = 0;
        answered_[i] = true;
        break;
      }
      case hal::EchoState::Timeout:
        mm_[i] = proto::kUsNoEcho;
        misses_[i] = 0;
        answered_[i] = true;
        break;
      case hal::EchoState::NoRise:
        if (misses_[i] < 255) ++misses_[i];
        if (misses_[i] >= c_.fault_after_misses) mm_[i] = proto::kUsAbsent;
        break;
    }
    active_ = -1;
  }
  const int8_t next = nextPresent();
  if (next < 0) return;
  hw_->trigger(static_cast<uint8_t>(next));
  active_ = next;
  trigger_ms_ = now_ms;
}

bool UltrasonicScheduler::anyFault() const {
  for (int i = 0; i < 4; ++i) {
    if (mm_[i] == proto::kUsAbsent) return true;
  }
  return false;
}

}  // namespace scr
