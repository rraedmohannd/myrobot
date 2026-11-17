#include "scr_safety.h"

namespace scr {

namespace {
bool validDistance(uint16_t mm) { return mm != proto::kUsAbsent; }
int16_t toArg(uint32_t v) { return v > 32767u ? static_cast<int16_t>(32767) : static_cast<int16_t>(v); }
}  // namespace

bool Safety::frontBlocked(const uint16_t us_mm[4], uint16_t threshold_mm, int16_t* nearest_mm) {
  bool any_present = false;
  uint16_t nearest = proto::kUsNoEcho;
  for (int i = 0; i < 3; ++i) {
    if (!validDistance(us_mm[i])) continue;
    any_present = true;
    if (us_mm[i] < nearest) nearest = us_mm[i];
  }
  if (!any_present) {
    if (nearest_mm) *nearest_mm = -1;
    return true;  // rule 5: no working front sensor counts as blocked
  }
  if (nearest_mm) *nearest_mm = toArg(nearest);
  return nearest < threshold_mm;
}

bool Safety::rearSensing(const uint16_t us_mm[4]) { return validDistance(us_mm[3]); }

bool Safety::rearBlocked(const uint16_t us_mm[4], uint16_t threshold_mm) {
  return validDistance(us_mm[3]) && us_mm[3] < threshold_mm;
}

void Safety::begin(uint32_t now_ms) {
  mode_ = proto::kModeSafe;
  left_ = right_ = 0;
  last_valid_ms_ = now_ms;
  link_lost_ = false;
  watchdog_flag_ = obstacle_flag_ = rear_flag_ = false;
  front_latch_ = rear_latch_ = false;
  hard_stop_ = true;
  q_head_ = q_count_ = 0;
}

void Safety::zeroTargets() {
  left_ = 0;
  right_ = 0;
  hard_stop_ = true;
}

void Safety::pushEvent(uint8_t code, int16_t arg) {
  if (q_count_ >= kQueue) return;  // full: drop the newest (flags still tell the state)
  queue_[(q_head_ + q_count_) % kQueue] = SafetyEvent{code, arg};
  ++q_count_;
}

bool Safety::popEvent(SafetyEvent* out) {
  if (q_count_ == 0) return false;
  *out = queue_[q_head_];
  q_head_ = static_cast<uint8_t>((q_head_ + 1) % kQueue);
  --q_count_;
  return true;
}

bool Safety::takeHardStop() {
  const bool h = hard_stop_;
  hard_stop_ = false;
  return h;
}

void Safety::onValidFrame(uint32_t now_ms) {
  last_valid_ms_ = now_ms;
  if (link_lost_) {
    link_lost_ = false;
    pushEvent(proto::kEventLinkRestored, 0);
  }
}

bool Safety::frontBlockedNow(const uint16_t us_mm[4], int16_t* nearest_mm) {
  // Latched: still blocked up to and including threshold + hysteresis.
  uint32_t limit = c_.obstacle_mm;
  if (front_latch_) limit += static_cast<uint32_t>(c_.clear_hysteresis_mm) + 1u;
  if (limit > proto::kUsAbsent) limit = proto::kUsAbsent;  // never treat "no echo" as blocked
  const bool blocked = frontBlocked(us_mm, static_cast<uint16_t>(limit), nearest_mm);
  if (!blocked) front_latch_ = false;
  return blocked;
}

bool Safety::rearBlockedNow(const uint16_t us_mm[4]) {
  if (!rearSensing(us_mm)) return true;
  uint32_t limit = c_.obstacle_mm;
  if (rear_latch_) limit += static_cast<uint32_t>(c_.clear_hysteresis_mm) + 1u;
  if (limit > proto::kUsAbsent) limit = proto::kUsAbsent;
  const bool blocked = rearBlocked(us_mm, static_cast<uint16_t>(limit));
  if (!blocked) rear_latch_ = false;
  return blocked;
}

void Safety::frontStop(int16_t nearest_mm) {
  zeroTargets();
  obstacle_flag_ = true;
  if (!front_latch_) {  // one event per blockage
    front_latch_ = true;
    pushEvent(proto::kEventObstacleStop, nearest_mm);
  }
}

void Safety::rearStop(const uint16_t us_mm[4]) {
  zeroTargets();
  rear_flag_ = true;
  if (!rear_latch_) {
    rear_latch_ = true;
    pushEvent(proto::kEventRearObstacleStop,
              rearSensing(us_mm) ? toArg(us_mm[3]) : static_cast<int16_t>(-1));
  }
}

uint8_t Safety::onDrive(int16_t left_mm_s, int16_t right_mm_s, const uint16_t us_mm[4]) {
  if (mode_ == proto::kModeSafe) {
    zeroTargets();
    return proto::kAckRejectedMode;
  }
  const int32_t sum = static_cast<int32_t>(left_mm_s) + static_cast<int32_t>(right_mm_s);
  if (sum > 0) {
    int16_t nearest = -1;
    if (frontBlockedNow(us_mm, &nearest)) {
      frontStop(nearest);
      return proto::kAckRejectedSafety;
    }
  } else if (sum < 0) {
    if (!rearSensing(us_mm) && !rear_latch_) {
      zeroTargets();  // no rear sensing at command time: rejected, no stop event
      return proto::kAckRejectedSafety;
    }
    if (rearBlockedNow(us_mm)) {
      rearStop(us_mm);
      return proto::kAckRejectedSafety;
    }
  }
  // Accepted. The flags clear; a blockage in progress (latch) is not forgotten.
  left_ = left_mm_s;
  right_ = right_mm_s;
  watchdog_flag_ = false;
  obstacle_flag_ = false;
  rear_flag_ = false;
  return proto::kAckOk;
}

void Safety::onStop() { zeroTargets(); }

void Safety::forceStop() { zeroTargets(); }

uint8_t Safety::onMode(uint8_t mode, bool motors_usable, bool closed_loop_ready) {
  if (mode > proto::kModeTest) return proto::kAckBadArgument;
  if (mode != proto::kModeSafe) {
    if (!motors_usable) return proto::kAckRejectedSafety;
    if (mode == proto::kModeDrive && !closed_loop_ready) return proto::kAckRejectedMode;
  }
  mode_ = mode;
  zeroTargets();  // every mode change starts from standstill
  return proto::kAckOk;
}

void Safety::tick(uint32_t now_ms, const uint16_t us_mm[4], float applied_left_mm_s,
                  float applied_right_mm_s) {
  // 1. Watchdog.
  const uint32_t silent_ms = now_ms - last_valid_ms_;
  if (!link_lost_ && silent_ms >= c_.watchdog_ms) {
    link_lost_ = true;
    watchdog_flag_ = true;
    zeroTargets();
    pushEvent(proto::kEventWatchdogStop, toArg(silent_ms));
  }

  // 2. Front obstacle (with hysteresis).
  int16_t nearest = -1;
  const bool front_blocked = frontBlockedNow(us_mm, &nearest);
  // Motion = the targets or the ramped outputs (a ramp-down is still motion). Deadband against
  // float dust; a symmetric turn in place sums to exactly 0.
  constexpr float kAppliedDeadbandMmS = 0.5f;
  const int32_t sum = static_cast<int32_t>(left_) + static_cast<int32_t>(right_);
  const float applied = applied_left_mm_s + applied_right_mm_s;
  const bool forward = sum > 0 || applied > kAppliedDeadbandMmS;
  const bool reverse = sum < 0 || applied < -kAppliedDeadbandMmS;
  if (forward && front_blocked) {
    frontStop(nearest);
  } else if (!front_blocked) {
    obstacle_flag_ = false;
  }

  // 3. Rear (with hysteresis; a rear sensor that is absent or faulty counts as blocked).
  const bool rear_blocked = rearBlockedNow(us_mm);
  if (reverse && rear_blocked) {
    rearStop(us_mm);
  } else if (!rear_blocked) {
    rear_flag_ = false;
  }
}

}  // namespace scr
