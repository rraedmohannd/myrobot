#include "scr_app.h"

#include <cmath>

#include "scr_version.h"

namespace scr {

namespace {
float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
int16_t clampI16(int32_t v, int32_t lim) {
  if (v > lim) v = lim;
  if (v < -lim) v = -lim;
  return static_cast<int16_t>(v);
}
int16_t saturateI16(int64_t v) {
  if (v > 32767) return 32767;
  if (v < -32768) return -32768;
  return static_cast<int16_t>(v);
}
float fb(bool b) { return b ? 1.0f : 0.0f; }
bool bf(float f) { return f >= 0.5f; }
int32_t fi(float f) { return static_cast<int32_t>(std::lround(f)); }

// Hard pulse range of the servo jog (interface spec A2): compiled, never a parameter.
constexpr int32_t kJogPulseMinUs = 500;
constexpr int32_t kJogPulseMaxUs = 2500;
constexpr int16_t kJogMaxStepUs = 50;
constexpr int16_t kImuRawMaxHz = 200;
}  // namespace

// ---- parameters <-> configuration -----------------------------------------------------------
int seedParamsFromConfig(const AppConfig& c, ParamStore* p) {
  using namespace params;
  int bad = 0;
  auto seed = [&](uint16_t id, float v) {
    if (!p->seedDefault(id, v)) ++bad;
  };
  seed(METRES_PER_TICK, c.metres_per_tick);
  seed(PID_KP, c.pid.kp);
  seed(PID_KI, c.pid.ki);
  seed(PID_KD, c.pid.kd);
  seed(PID_KFF, c.pid.kff);
  seed(SPEED_FILTER_ALPHA, c.speed_filter_alpha);
  seed(RAMP_MM_S2, c.ramp_mm_s2);
  seed(MAX_SPEED_MM_S, static_cast<float>(c.max_speed_mm_s));
  seed(TEST_MAX_DUTY, c.test_max_duty);
  seed(MOTOR_MAX_DUTY, c.motor_max_duty);
  seed(MOTOR_RATED_V, c.motor_rated_v);
  seed(V_DRIVER_DROP, c.v_driver_drop);
  seed(MOTOR_INVERT_LEFT, fb(c.motor_invert_left));
  seed(MOTOR_INVERT_RIGHT, fb(c.motor_invert_right));
  seed(ENCODER_INVERT_FL, fb(c.encoder_invert[0]));
  seed(ENCODER_INVERT_RL, fb(c.encoder_invert[1]));
  seed(ENCODER_INVERT_FR, fb(c.encoder_invert[2]));
  seed(ENCODER_INVERT_RR, fb(c.encoder_invert[3]));
  seed(SERVO_MIN_CDEG, static_cast<float>(c.servo.min_cdeg));
  seed(SERVO_MAX_CDEG, static_cast<float>(c.servo.max_cdeg));
  seed(SERVO_CENTER_PULSE_US, c.servo.center_pulse_us);
  seed(SERVO_US_PER_DEG, c.servo.us_per_deg);
  seed(SERVO_INVERT, fb(c.servo.invert));
  seed(SERVO_SLEW_CDEG_PER_S, c.servo.slew_cdeg_per_s);
  seed(SERVO_PULSE_MIN_US, static_cast<float>(c.servo.pulse_min_us));
  seed(SERVO_PULSE_MAX_US, static_cast<float>(c.servo.pulse_max_us));
  seed(OBSTACLE_THRESHOLD_MM, static_cast<float>(c.safety.obstacle_mm));
  seed(OBSTACLE_CLEAR_HYSTERESIS_MM, static_cast<float>(c.safety.clear_hysteresis_mm));
  seed(LOW_BATTERY_MV, static_cast<float>(c.battery.low_mv));
  seed(LOW_BATTERY_RECOVER_MV, static_cast<float>(c.battery.recover_mv));
  seed(BATTERY_CUTOFF_MV, static_cast<float>(c.battery_cutoff_mv));
  seed(INA219_SHUNT_OHMS, c.ina219_shunt_ohms);
  seed(BATTERY_VOLTAGE_SCALE, c.battery_voltage_scale);
  seed(BATTERY_CURRENT_SCALE, c.battery_current_scale);
  seed(INA219_INVERT_CURRENT, fb(c.ina219_invert_current));
  seed(GYRO_SCALE, c.gyro_scale);
  seed(SLIP_RATIO, c.slip.ratio);
  seed(SLIP_MIN_TICKS, static_cast<float>(c.slip.min_ticks));
  seed(ENCODER_FAULT_MIN_DUTY, c.encoder_fault.min_duty);
  seed(ENCODER_FAULT_TIMEOUT_MS, static_cast<float>(c.encoder_fault.timeout_ms));
  return bad;
}

void paramsToConfig(const ParamStore& p, AppConfig* c) {
  using namespace params;
  c->metres_per_tick = p.get(METRES_PER_TICK);
  c->pid.kp = p.get(PID_KP);
  c->pid.ki = p.get(PID_KI);
  c->pid.kd = p.get(PID_KD);
  c->pid.kff = p.get(PID_KFF);
  c->speed_filter_alpha = p.get(SPEED_FILTER_ALPHA);
  c->ramp_mm_s2 = p.get(RAMP_MM_S2);
  c->max_speed_mm_s = static_cast<int16_t>(fi(p.get(MAX_SPEED_MM_S)));
  c->test_max_duty = p.get(TEST_MAX_DUTY);
  c->motor_max_duty = p.get(MOTOR_MAX_DUTY);
  c->motor_rated_v = p.get(MOTOR_RATED_V);
  c->v_driver_drop = p.get(V_DRIVER_DROP);
  c->motor_invert_left = bf(p.get(MOTOR_INVERT_LEFT));
  c->motor_invert_right = bf(p.get(MOTOR_INVERT_RIGHT));
  c->encoder_invert[0] = bf(p.get(ENCODER_INVERT_FL));
  c->encoder_invert[1] = bf(p.get(ENCODER_INVERT_RL));
  c->encoder_invert[2] = bf(p.get(ENCODER_INVERT_FR));
  c->encoder_invert[3] = bf(p.get(ENCODER_INVERT_RR));
  c->servo.min_cdeg = static_cast<int16_t>(fi(p.get(SERVO_MIN_CDEG)));
  c->servo.max_cdeg = static_cast<int16_t>(fi(p.get(SERVO_MAX_CDEG)));
  c->servo.center_pulse_us = p.get(SERVO_CENTER_PULSE_US);
  c->servo.us_per_deg = p.get(SERVO_US_PER_DEG);
  c->servo.invert = bf(p.get(SERVO_INVERT));
  c->servo.slew_cdeg_per_s = p.get(SERVO_SLEW_CDEG_PER_S);
  c->servo.pulse_min_us = static_cast<uint16_t>(fi(p.get(SERVO_PULSE_MIN_US)));
  c->servo.pulse_max_us = static_cast<uint16_t>(fi(p.get(SERVO_PULSE_MAX_US)));
  c->safety.obstacle_mm = static_cast<uint16_t>(fi(p.get(OBSTACLE_THRESHOLD_MM)));
  c->safety.clear_hysteresis_mm = static_cast<uint16_t>(fi(p.get(OBSTACLE_CLEAR_HYSTERESIS_MM)));
  c->battery.low_mv = static_cast<uint16_t>(fi(p.get(LOW_BATTERY_MV)));
  c->battery.recover_mv = static_cast<uint16_t>(fi(p.get(LOW_BATTERY_RECOVER_MV)));
  c->battery_cutoff_mv = static_cast<uint16_t>(fi(p.get(BATTERY_CUTOFF_MV)));
  c->ina219_shunt_ohms = p.get(INA219_SHUNT_OHMS);
  c->battery_voltage_scale = p.get(BATTERY_VOLTAGE_SCALE);
  c->battery_current_scale = p.get(BATTERY_CURRENT_SCALE);
  c->ina219_invert_current = bf(p.get(INA219_INVERT_CURRENT));
  c->gyro_scale = p.get(GYRO_SCALE);
  c->slip.ratio = p.get(SLIP_RATIO);
  c->slip.min_ticks = fi(p.get(SLIP_MIN_TICKS));
  c->encoder_fault.min_duty = p.get(ENCODER_FAULT_MIN_DUTY);
  c->encoder_fault.timeout_ms = static_cast<uint32_t>(fi(p.get(ENCODER_FAULT_TIMEOUT_MS)));
}

// ---- construction ---------------------------------------------------------------------------
FirmwareApp::FirmwareApp(const AppConfig& config, const hal::Hardware& hw)
    : cfg_(config),
      hw_(hw),
      safety_(config.safety),
      pid_left_(config.pid),
      pid_right_(config.pid),
      ramp_left_(config.ramp_mm_s2),
      ramp_right_(config.ramp_mm_s2),
      servo_(config.servo),
      gyro_cal_(config.gyro_cal),
      battery_(config.battery),
      enc_fault_{EncoderFaultDetector(config.encoder_fault), EncoderFaultDetector(config.encoder_fault),
                 EncoderFaultDetector(config.encoder_fault), EncoderFaultDetector(config.encoder_fault)},
      slip_{SlipDetector(config.slip), SlipDetector(config.slip)},
      vcomp_(VoltageCompConfig{}),
      ultrasonic_(config.ultrasonic, hw.ultrasonic) {
  seed_failures_ = seedParamsFromConfig(config, &params_);
  applyParams();
}

// Pushes the parameter store into the configuration and every control block. Called at start,
// after the NVS load and after every accepted PARAM_SET (only while the tracks do not move).
void FirmwareApp::applyParams() {
  bool old_invert[4];
  for (int i = 0; i < 4; ++i) old_invert[i] = cfg_.encoder_invert[i];
  paramsToConfig(params_, &cfg_);
  // An ENCODER_INVERT_* change flips the sign of that cumulative count: without a new baseline
  // the next delta would be -2 x count, a phantom track speed that the PID answers with full duty
  // the wrong way (safety review, 2026-11-17). The next reading starts a new baseline instead.
  for (int i = 0; i < 4; ++i) {
    if (old_invert[i] != cfg_.encoder_invert[i]) have_enc_ = false;
  }
  safety_.setObstacleConfig(cfg_.safety.obstacle_mm, cfg_.safety.clear_hysteresis_mm);
  PidGains g = cfg_.pid;
  pid_left_.setGains(g);
  pid_right_.setGains(g);
  ramp_left_.setMaxRate(cfg_.ramp_mm_s2);
  ramp_right_.setMaxRate(cfg_.ramp_mm_s2);
  servo_.setConfig(cfg_.servo);
  battery_.setConfig(cfg_.battery);
  for (EncoderFaultDetector& d : enc_fault_) d.setConfig(cfg_.encoder_fault);
  for (SlipDetector& d : slip_) d.setConfig(cfg_.slip);
  VoltageCompConfig vc;
  vc.motor_max_duty = cfg_.motor_max_duty;
  vc.motor_rated_v = cfg_.motor_rated_v;
  vc.v_driver_drop = cfg_.v_driver_drop;
  vcomp_.setConfig(vc);
}

void FirmwareApp::begin(uint32_t now_ms) {
  // A driver whose begin() fails (pin not assigned, device missing) is never touched again.
  motors_ok_ = hw_.motors != nullptr && hw_.motors->begin();
  if (motors_ok_) {
    hw_.motors->setEnabled(false);
    hw_.motors->setTrackDuty(0.0f, 0.0f);
  }
  encoders_ok_ = hw_.encoders != nullptr && hw_.encoders->begin();
  imu_present_ = hw_.imu != nullptr && hw_.imu->begin();
  imu_ok_ = imu_present_;
  servo_ok_ = hw_.servo != nullptr && hw_.servo->begin();
  power_present_ = hw_.power != nullptr && hw_.power->begin();
  power_ok_ = false;
  ultrasonic_.begin(hw_.ultrasonic != nullptr && hw_.ultrasonic->begin());

  // Parameters: compiled defaults, then whatever NVS holds (each value checked against the
  // compiled bounds again; a bad stored value is ignored).
  storage_ok_ = hw_.storage != nullptr && hw_.storage->begin();
  if (storage_ok_) params_.load(hw_.storage);
  applyParams();

  safety_.begin(now_ms);  // power-on state: SAFE, whatever the reset reason was
  last_tick_ms_ = now_ms;
  last_power_ms_ = now_ms;
  last_enc_change_ms_ = now_ms;
  last_error_report_ms_ = now_ms;
  started_ = true;
  sendEvent(now_ms, proto::kEventBoot, 0);
  if (!imu_present_) sendEvent(now_ms, proto::kEventImuFault, 0);
  // DIAG_REPORT once at boot, after EVENT BOOT, as soon as the boot I2C scan is finished.
  if (hw_.system != nullptr) hw_.system->requestI2cScan();
  diag_pending_ = true;
  diag_deadline_ms_ = now_ms + cfg_.diag_scan_wait_ms;
}

// ---- link -----------------------------------------------------------------------------------
void FirmwareApp::send(const proto::Message& msg) {
  if (hw_.link == nullptr) return;
  uint8_t frame[proto::kMaxFrame];
  const size_t n = proto::encodeFrame(msg, tx_seq_, frame, sizeof(frame));
  if (n == 0) return;
  ++tx_seq_;
  hw_.link->write(frame, n);
}

void FirmwareApp::sendEvent(uint32_t now_ms, uint8_t code, int16_t arg) {
  send(proto::makeEvent(now_ms, code, arg));
}

void FirmwareApp::sendAck(uint8_t type, uint8_t seq, uint8_t status) {
  send(proto::makeAck(type, seq, status));
}

void FirmwareApp::receive(uint32_t now_ms) {
  if (hw_.link == nullptr) return;
  uint8_t buf[64];
  for (int chunk = 0; chunk < 8; ++chunk) {  // bounded work per tick
    const size_t n = hw_.link->read(buf, sizeof(buf));
    if (n == 0) break;
    for (size_t i = 0; i < n; ++i) {
      const proto::FrameDecoder::Feed r = decoder_.feed(buf[i]);
      if (r == proto::FrameDecoder::Feed::Frame) {
        handleFrame(now_ms, decoder_.message(), decoder_.seq());
      } else if (r == proto::FrameDecoder::Feed::Bad) {
        // Dropped and counted; never feeds the watchdog.
        ++protocol_errors_total_;
        ++protocol_errors_unreported_;
        if (decoder_.lastError() == proto::DecodeResult::UnknownType) {
          sendAck(decoder_.rawType(), decoder_.seq(), proto::kAckUnknownType);
        } else if (decoder_.lastError() == proto::DecodeResult::BadLength) {
          sendAck(decoder_.rawType(), decoder_.seq(), proto::kAckBadArgument);
        }
      }
    }
  }
  if (protocol_errors_unreported_ > 0 &&
      now_ms - last_error_report_ms_ >= cfg_.protocol_error_report_ms) {
    sendEvent(now_ms, proto::kEventProtocolError, saturateI16(protocol_errors_unreported_));
    protocol_errors_unreported_ = 0;
    last_error_report_ms_ = now_ms;
  }
}

bool FirmwareApp::encoderFaultLatched() const {
  for (int i = 0; i < 4; ++i) {
    if (enc_fault_[i].faulty()) return true;
  }
  return false;
}

// Strict "moving": a target, a ramp output, or any encoder change within still_ms.
bool FirmwareApp::moving(uint32_t now_ms) const {
  if (tracksMoving()) return true;
  if (have_enc_ && now_ms - last_enc_change_ms_ < cfg_.still_ms) return true;
  return false;
}

// Interface spec A2 "no change while the tracks move": a target is non-zero or the measured
// track speed is above 10 mm/s (ramp outputs count as targets).
bool FirmwareApp::tracksMoving() const {
  if (safety_.targetLeft() != 0 || safety_.targetRight() != 0) return true;
  if (ramp_left_.value() != 0.0f || ramp_right_.value() != 0.0f) return true;
  if (std::fabs(speed_left_m_s_) > cfg_.busy_speed_m_s) return true;
  if (std::fabs(speed_right_m_s_) > cfg_.busy_speed_m_s) return true;
  return false;
}

void FirmwareApp::handleFrame(uint32_t now_ms, const proto::Message& msg, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(msg.type);
  switch (msg.type) {
    case proto::MsgType::Telemetry:
    case proto::MsgType::Event:
    case proto::MsgType::Ack:
    case proto::MsgType::ParamReport:
    case proto::MsgType::DiagReport:
    case proto::MsgType::ImuRaw:
    case proto::MsgType::UsRaw:
      // ESP32 -> Pi messages are not commands: counted as protocol errors, no watchdog feed.
      ++protocol_errors_total_;
      ++protocol_errors_unreported_;
      sendAck(type, seq, proto::kAckUnknownType);
      return;
    default:
      break;
  }

  safety_.onValidFrame(now_ms);

  switch (msg.type) {
    case proto::MsgType::Drive: {
      if (gyro_cal_.running()) {
        safety_.onStop();
        sendAck(type, seq, proto::kAckBusy);
        break;
      }
      if (safety_.mode() == proto::kModeDrive && encoderFaultLatched()) {
        // Closed loop with a dead encoder: refused until the encoder counts again
        // (TEST mode still works, to find out which one it is).
        safety_.onStop();
        sendAck(type, seq, proto::kAckRejectedSafety);
        break;
      }
      const uint8_t st =
          safety_.onDrive(msg.drive.left_mm_s, msg.drive.right_mm_s, ultrasonic_.distancesMm());
      if (st != proto::kAckOk) sendAck(type, seq, st);
      break;
    }
    case proto::MsgType::Stop:
      safety_.onStop();
      sendAck(type, seq, proto::kAckOk);
      break;
    case proto::MsgType::Servo:
      if (servo_ok_ && !cfg_.permanent_safe) {
        servo_.setTarget(msg.servo.pan_cdeg);
        if (!jog_active_) jog_hold_ = false;  // the normal servo control takes over again
      } else {
        sendAck(type, seq, proto::kAckRejectedSafety);  // no usable servo output
      }
      break;
    case proto::MsgType::Mode: {
      uint8_t st;
      if (msg.mode.mode != proto::kModeSafe && msg.mode.mode <= proto::kModeTest &&
          (cfg_.permanent_safe || cutoff_)) {
        st = proto::kAckRejectedSafety;  // CI pin build, or battery below the hard cut-off
      } else {
        const bool closed_loop_ready = encoders_ok_ && cfg_.metres_per_tick > 0.0f;
        st = safety_.onMode(msg.mode.mode, motors_ok_, closed_loop_ready);
      }
      sendAck(type, seq, st);
      break;
    }
    case proto::MsgType::Heartbeat:
      break;
    case proto::MsgType::CalibrateGyro:
      if (gyro_cal_.running() || moving(now_ms)) {
        sendAck(type, seq, proto::kAckBusy);
      } else if (!imu_ok_) {
        sendAck(type, seq, proto::kAckOk);
        sendEvent(now_ms, proto::kEventImuFault, 0);
        sendEvent(now_ms, proto::kEventGyroCalFailed, 0);
      } else {
        gyro_cal_.start();
        sendAck(type, seq, proto::kAckOk);
      }
      break;
    case proto::MsgType::ParamSet:
      handleParamSet(now_ms, msg.param_set, seq);
      break;
    case proto::MsgType::ParamGet:
      handleParamGet(msg.param_get, seq);
      break;
    case proto::MsgType::ParamSave:
      handleParamSave(now_ms, seq);
      break;
    case proto::MsgType::DiagRequest:
      // Answered from the control tick once the fresh I2C scan (another task) is done.
      if (hw_.system != nullptr) hw_.system->requestI2cScan();
      diag_pending_ = true;
      diag_deadline_ms_ = now_ms + cfg_.diag_scan_wait_ms;
      break;
    case proto::MsgType::Bringup:
      handleBringup(now_ms, msg.bringup, seq);
      break;
    default:
      break;
  }
}

// ---- parameters -----------------------------------------------------------------------------
void FirmwareApp::sendParamReport(int i) {
  const params::ParamDef& d = ParamStore::def(i);
  send(proto::makeParamReport(d.id, params_.valueAt(i), d.min, d.max, params_.sourceAt(i)));
}

void FirmwareApp::handleParamSet(uint32_t now_ms, const proto::ParamSet& m, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(proto::MsgType::ParamSet);
  // Order of the checks: unknown id and bounds first (they do not depend on the state), then
  // BUSY (tracks moving, or a PARAM_SAVE in progress). Nothing changes unless the answer is OK.
  uint8_t st = ParamStore::check(m.id, m.value);
  if (st == proto::kAckOk && (tracksMoving() || params_.saving())) st = proto::kAckBusy;
  if (st != proto::kAckOk) {
    sendAck(type, seq, st);
    return;
  }
  bool changed = false;
  params_.set(m.id, m.value, &changed);
  applyParams();
  // A closed loop must never run uncalibrated: METRES_PER_TICK set to 0 in MODE DRIVE -> SAFE.
  if (safety_.mode() == proto::kModeDrive && !(cfg_.metres_per_tick > 0.0f)) {
    safety_.onMode(proto::kModeSafe, motors_ok_, false);
  }
  sendAck(type, seq, proto::kAckOk);
  sendParamReport(ParamStore::indexOf(m.id));
  if (changed) sendEvent(now_ms, proto::kEventParamChanged, static_cast<int16_t>(m.id));
}

void FirmwareApp::handleParamGet(const proto::ParamGet& m, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(proto::MsgType::ParamGet);
  if (m.id == proto::kParamAll) {
    for (int i = 0; i < ParamStore::count(); ++i) sendParamReport(i);
    sendAck(type, seq, proto::kAckOk);
    return;
  }
  const int i = ParamStore::indexOf(m.id);
  if (i < 0) {
    sendAck(type, seq, proto::kAckUnknownParam);
    return;
  }
  sendParamReport(i);
  sendAck(type, seq, proto::kAckOk);
}

void FirmwareApp::handleParamSave(uint32_t now_ms, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(proto::MsgType::ParamSave);
  // "SAFE or IDLE": drive mode SAFE, or DRIVE/TEST with both targets 0 and the tracks still.
  const bool idle = safety_.targetLeft() == 0 && safety_.targetRight() == 0 && !moving(now_ms);
  if (safety_.mode() != proto::kModeSafe && !idle) {
    sendAck(type, seq, proto::kAckRejectedMode);
    return;
  }
  if (params_.saving()) {  // one save at a time
    sendAck(type, seq, proto::kAckBusy);
    return;
  }
  if (!storage_ok_) {  // no storage: BUSY ("not done"), nothing is marked saved
    sendAck(type, seq, proto::kAckBusy);
    return;
  }
  // The save runs one NVS key per control tick (stepParamSave), so no tick blocks for the whole
  // save and every task keeps feeding the task watchdog. The ACK comes when it is finished
  // (about 41 ticks = 0.41 s): OK, or BUSY when a write failed.
  params_.beginSave();
  save_seq_ = seq;
}

void FirmwareApp::stepParamSave() {
  if (!params_.saving()) return;
  const ParamStore::SaveStep st = params_.saveStep(hw_.storage);
  if (st == ParamStore::SaveStep::InProgress) return;
  sendAck(static_cast<uint8_t>(proto::MsgType::ParamSave), save_seq_,
          st == ParamStore::SaveStep::Done ? proto::kAckOk : proto::kAckBusy);
}

// Speed-sign mismatch (safety review 5b), MODE DRIVE only: a track driven with |duty| above
// sign_fault_min_duty whose measured speed has the opposite sign and is faster than
// sign_fault_min_speed_m_s for sign_fault_ms means an inverted motor or encoder: the closed loop
// would run away. -> STOP, MODE SAFE, EVENT ENCODER_FAULT arg 10 (left) / 11 (right).
// Not in TEST mode: motor-test must be able to discover the inversions.
void FirmwareApp::checkSpeedSign(uint32_t now_ms) {
  for (int side = 0; side < 2; ++side) {
    const float duty = side == 0 ? duty_left_ : duty_right_;
    const float speed = side == 0 ? speed_left_m_s_ : speed_right_m_s_;
    const bool bad = encoders_ok_ && motors_enabled_ && safety_.mode() == proto::kModeDrive &&
                     std::fabs(duty) > cfg_.sign_fault_min_duty && duty * speed < 0.0f &&
                     std::fabs(speed) > cfg_.sign_fault_min_speed_m_s;
    if (!bad) {
      sign_timing_[side] = false;
      continue;
    }
    if (!sign_timing_[side]) {
      sign_timing_[side] = true;
      sign_since_ms_[side] = now_ms;
      continue;
    }
    if (now_ms - sign_since_ms_[side] >= cfg_.sign_fault_ms) {
      sign_timing_[side] = false;
      safety_.onMode(proto::kModeSafe, motors_ok_, false);  // targets zero, drivers off
      sendEvent(now_ms, proto::kEventEncoderFault, static_cast<int16_t>(10 + side));
    }
  }
}

// ---- diagnostics ----------------------------------------------------------------------------
void FirmwareApp::sendDiag(uint32_t now_ms) {
  proto::DiagReport d{};
  d.fw_major = SCR_FW_VERSION_MAJOR;
  d.fw_minor = SCR_FW_VERSION_MINOR;
  d.fw_patch = SCR_FW_VERSION_PATCH;
  d.git_hash = static_cast<uint32_t>(SCR_GIT_HASH);
  d.build_env = cfg_.build_env;
  d.driver_type = cfg_.driver_type;
  d.diag_flags = cfg_.static_diag_flags;
  if (params_.anyFromNvs()) d.diag_flags |= proto::kDiagParamsFromNvs;
  d.param_hash = params_.hash();
  d.uptime_ms = now_ms;
  uint8_t sensors = 0;
  if (imu_present_) sensors |= proto::kSensorImu;
  if (power_present_) sensors |= proto::kSensorIna219;
  for (uint8_t i = 0; i < 4; ++i) {
    if (ultrasonic_.everAnswered(i)) sensors |= static_cast<uint8_t>(proto::kSensorUs0 << i);
  }
  if (hw_.system != nullptr) {
    d.reset_reason = hw_.system->resetReason();
    d.free_heap = hw_.system->freeHeap();
    hw_.system->loopStats(&d.loop_period_max_us, &d.loop_jitter_us);
    d.i2c_count = hw_.system->i2cScanResult(d.i2c_addr, 8);
    if (hw_.system->expanderPresent()) sensors |= proto::kSensorExpander;
  }
  d.sensors = sensors;
  send(proto::makeDiagReport(d));
}

// ---- bring-up (MODE TEST only) --------------------------------------------------------------
void FirmwareApp::handleBringup(uint32_t now_ms, const proto::Bringup& m, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(proto::MsgType::Bringup);
  if (m.kind == proto::kBringupOff) {
    endBringup();
    sendAck(type, seq, proto::kAckOk);
    return;
  }
  if (m.kind > proto::kBringupServoJog) {
    sendAck(type, seq, proto::kAckBadArgument);
    return;
  }
  if (safety_.mode() != proto::kModeTest || cfg_.permanent_safe) {
    sendAck(type, seq, proto::kAckRejectedMode);
    return;
  }
  switch (m.kind) {
    case proto::kBringupImuRaw:
      if (m.arg < 1 || m.arg > kImuRawMaxHz) {
        sendAck(type, seq, proto::kAckBadArgument);
        return;
      }
      if (!imu_present_) {
        sendAck(type, seq, proto::kAckRejectedSafety);  // no usable device
        return;
      }
      // Rates above the control rate are sent at the control rate (one frame per tick).
      imu_raw_period_ms_ = static_cast<uint32_t>(1000 / m.arg);
      if (imu_raw_period_ms_ == 0) imu_raw_period_ms_ = 1;
      last_imu_raw_ms_ = now_ms - imu_raw_period_ms_;
      sendAck(type, seq, proto::kAckOk);
      return;
    case proto::kBringupUsRaw:
      if (hw_.ultrasonic == nullptr) {
        sendAck(type, seq, proto::kAckRejectedSafety);
        return;
      }
      us_raw_on_ = true;
      last_us_round_ = ultrasonic_.rounds();
      sendAck(type, seq, proto::kAckOk);
      return;
    case proto::kBringupServoJog: {
      if (m.arg < -kJogMaxStepUs || m.arg > kJogMaxStepUs) {
        sendAck(type, seq, proto::kAckBadArgument);
        return;
      }
      if (!servo_ok_) {
        sendAck(type, seq, proto::kAckRejectedSafety);
        return;
      }
      if (!jog_active_ && !jog_hold_) {
        // Start from the pulse the servo has now, or the centre when it never had one.
        jog_pulse_us_ = last_servo_pulse_us_ != 0 ? last_servo_pulse_us_
                                                  : static_cast<uint16_t>(fi(cfg_.servo.center_pulse_us));
      }
      int32_t p = static_cast<int32_t>(jog_pulse_us_) + m.arg;
      if (p < kJogPulseMinUs) p = kJogPulseMinUs;
      if (p > kJogPulseMaxUs) p = kJogPulseMaxUs;
      jog_pulse_us_ = static_cast<uint16_t>(p);
      jog_active_ = true;
      jog_hold_ = true;
      hw_.servo->writePulseUs(jog_pulse_us_);
      last_servo_pulse_us_ = jog_pulse_us_;
      sendAck(type, seq, proto::kAckOk);
      sendEvent(now_ms, proto::kEventServoPulse, static_cast<int16_t>(jog_pulse_us_));
      return;
    }
    default:
      sendAck(type, seq, proto::kAckBadArgument);
      return;
  }
}

// Every stream off. The jogged servo keeps its pulse (jog_hold_) until the next SERVO command,
// so ending the jog never makes the camera jump.
void FirmwareApp::endBringup() {
  imu_raw_period_ms_ = 0;
  us_raw_on_ = false;
  jog_active_ = false;
}

void FirmwareApp::stepBringup(uint32_t now_ms) {
  if (!bringupActive()) return;
  // Leaving MODE TEST (for any reason, including the battery cut-off) or a watchdog stop ends
  // every bring-up stream.
  if (safety_.mode() != proto::kModeTest || safety_.linkLost()) {
    endBringup();
    return;
  }
  if (imu_raw_period_ms_ != 0 && imu_ok_ && now_ms - last_imu_raw_ms_ >= imu_raw_period_ms_) {
    last_imu_raw_ms_ = now_ms;
    proto::ImuRaw r{};
    r.t_ms = now_ms;
    for (int i = 0; i < 3; ++i) {
      r.gyro_urad_s[i] = imu_.gyro_urad_s[i];
      r.accel_mg[i] = imu_.accel_mg[i];
    }
    send(proto::makeImuRaw(r));
  }
  if (us_raw_on_ && ultrasonic_.rounds() != last_us_round_) {
    last_us_round_ = ultrasonic_.rounds();
    proto::UsRaw r{};
    r.t_ms = now_ms;
    const uint16_t* mm = ultrasonic_.distancesMm();
    for (uint8_t i = 0; i < 4; ++i) {
      r.dist_mm[i] = mm[i];
      r.misses[i] = ultrasonic_.misses(i);
    }
    send(proto::makeUsRaw(r));
  }
}

// ---- sensors --------------------------------------------------------------------------------
void FirmwareApp::readSensors(uint32_t now_ms, float dt_s) {
  enc_changed_this_tick_ = false;
  if (encoders_ok_) {
    int32_t raw[4] = {0, 0, 0, 0};
    hw_.encoders->read(raw);
    for (int i = 0; i < 4; ++i) {
      const int32_t v = cfg_.encoder_invert[i] ? static_cast<int32_t>(0u - static_cast<uint32_t>(raw[i])) : raw[i];
      enc_delta_[i] = have_enc_ ? static_cast<int32_t>(static_cast<uint32_t>(v) - static_cast<uint32_t>(enc_[i])) : 0;
      enc_[i] = v;
      if (enc_delta_[i] != 0) enc_changed_this_tick_ = true;
    }
    have_enc_ = true;
    if (enc_changed_this_tick_) last_enc_change_ms_ = now_ms;
    // Track speed = mean of the front and the rear encoder of that side (docs/ODOMETRY.md).
    const float k = cfg_.metres_per_tick / dt_s;
    const float left = 0.5f * (static_cast<float>(enc_delta_[0]) + static_cast<float>(enc_delta_[1])) * k;
    const float right = 0.5f * (static_cast<float>(enc_delta_[2]) + static_cast<float>(enc_delta_[3])) * k;
    const float a = clampf(cfg_.speed_filter_alpha, 0.0f, 1.0f);
    speed_left_m_s_ += a * (left - speed_left_m_s_);
    speed_right_m_s_ += a * (right - speed_right_m_s_);
    // Slip: front vs rear encoder of one side (interface spec A5).
    if (slip_[0].update(now_ms, enc_delta_[0], enc_delta_[1])) sendEvent(now_ms, proto::kEventSlip, 0);
    if (slip_[1].update(now_ms, enc_delta_[2], enc_delta_[3])) sendEvent(now_ms, proto::kEventSlip, 1);
  }

  if (imu_present_) {
    hal::ImuSample s{};
    const bool ok = hw_.imu->read(&s);  // never blocks: copies the I2C task's latest sample
    if (ok) {
      imu_ = s;
    } else if (imu_ok_) {
      sendEvent(now_ms, proto::kEventImuFault, 0);  // once per failure
    }
    imu_ok_ = ok;
  }

  ultrasonic_.tick(now_ms);

  if (now_ms - last_power_ms_ >= cfg_.power_period_ms) {
    readPower(now_ms);
  }
}

void FirmwareApp::readPower(uint32_t now_ms) {
  const float dt_s = static_cast<float>(now_ms - last_power_ms_) / 1000.0f;
  last_power_ms_ = now_ms;
  float volts = 0.0f;
  if (power_present_) {
    uint16_t mv = 0;
    int32_t shunt_uv = 0;
    power_ok_ = hw_.power->read(&mv, &shunt_uv) && cfg_.ina219_shunt_ohms > 0.0f;
    if (power_ok_) {
      const float v_mv = static_cast<float>(mv) * cfg_.battery_voltage_scale;
      // uV / ohm = uA; / 1000 = mA
      float ma = static_cast<float>(shunt_uv) / cfg_.ina219_shunt_ohms / 1000.0f * cfg_.battery_current_scale;
      if (cfg_.ina219_invert_current) ma = -ma;
      battery_mv_ = static_cast<uint16_t>(clampf(v_mv + 0.5f, 0.0f, 65535.0f));
      battery_ma_ = saturateI16(static_cast<int64_t>(std::lround(clampf(ma, -40000.0f, 40000.0f))));
      volts = v_mv / 1000.0f;
    }
    if (battery_.update(now_ms, battery_mv_, power_ok_)) {
      sendEvent(now_ms, proto::kEventLowBattery, saturateI16(battery_mv_));
    }
  } else {
    power_ok_ = false;
  }
  // Voltage compensation: no reading -> conservative full-pack constant (flag 13).
  vcomp_.update(volts, power_ok_, dt_s);

  // Battery hard cut-off: below BATTERY_CUTOFF_MV for battery_cutoff_hold_ms -> SAFE.
  if (cfg_.battery_cutoff_mv > 0 && power_ok_) {
    if (battery_mv_ < cfg_.battery_cutoff_mv) {
      if (!cutoff_below_) {
        cutoff_below_ = true;
        cutoff_below_since_ms_ = now_ms;
      }
      if (!cutoff_ && now_ms - cutoff_below_since_ms_ >= cfg_.battery_cutoff_hold_ms) {
        cutoff_ = true;
        safety_.onMode(proto::kModeSafe, motors_ok_, false);  // drivers off, targets zero
        sendEvent(now_ms, proto::kEventBatteryCutoff, saturateI16(battery_mv_));
      }
    } else {
      cutoff_below_ = false;
      if (cutoff_ && battery_mv_ >= static_cast<uint32_t>(cfg_.battery_cutoff_mv) + cfg_.battery_cutoff_recover_mv) {
        cutoff_ = false;  // MODE DRIVE/TEST accepted again (the robot stays in SAFE until asked)
      }
    }
  } else if (cfg_.battery_cutoff_mv == 0) {
    cutoff_ = false;
    cutoff_below_ = false;
  }
}

void FirmwareApp::stepGyroCalibration(uint32_t now_ms) {
  if (!gyro_cal_.running()) return;
  if (!imu_ok_) gyro_cal_.abort();
  const bool moved = enc_changed_this_tick_ || safety_.targetLeft() != 0 || safety_.targetRight() != 0;
  switch (gyro_cal_.addSample(imu_.gyro_z_urad_s, moved)) {
    case GyroBiasCalibrator::State::Done:
      // arg: bias in 0.1 mrad/s = 100 urad/s
      sendEvent(now_ms, proto::kEventGyroCalDone, saturateI16(gyro_cal_.biasUradS() / 100));
      break;
    case GyroBiasCalibrator::State::Failed:
      sendEvent(now_ms, proto::kEventGyroCalFailed, 0);
      break;
    default:
      break;
  }
}

// ---- outputs --------------------------------------------------------------------------------
void FirmwareApp::driveOutputs(float dt_s) {
  if (safety_.takeHardStop()) {
    // STOP, watchdog, obstacle, mode change, rejected DRIVE: no ramp-down.
    ramp_left_.reset(0.0f);
    ramp_right_.reset(0.0f);
    pid_left_.reset();
    pid_right_.reset();
  }

  const uint8_t mode = safety_.mode();
  const bool enable = motors_ok_ && mode != proto::kModeSafe && !cfg_.permanent_safe;
  // Interface spec A8: one cap for both modes, from the (filtered) battery voltage.
  const float cap = clampf(vcomp_.dutyCap(), 0.0f, 1.0f);
  float duty_l = 0.0f;
  float duty_r = 0.0f;

  if (enable) {
    const int16_t tl = clampI16(safety_.targetLeft(), cfg_.max_speed_mm_s);
    const int16_t tr = clampI16(safety_.targetRight(), cfg_.max_speed_mm_s);
    const float rl = ramp_left_.update(static_cast<float>(tl), dt_s);
    const float rr = ramp_right_.update(static_cast<float>(tr), dt_s);
    if (mode == proto::kModeDrive) {
      pid_left_.setOutputLimits(-cap, cap);  // anti-windup works against the real limit
      pid_right_.setOutputLimits(-cap, cap);
      if (tl == 0 && rl == 0.0f) {
        pid_left_.reset();
      } else {
        duty_l = pid_left_.update(rl / 1000.0f, speed_left_m_s_, dt_s);
      }
      if (tr == 0 && rr == 0.0f) {
        pid_right_.reset();
      } else {
        duty_r = pid_right_.update(rr / 1000.0f, speed_right_m_s_, dt_s);
      }
    } else {  // TEST: open loop, additionally capped by TEST_MAX_DUTY
      float lim = clampf(cfg_.test_max_duty, 0.0f, 1.0f);
      if (lim > cap) lim = cap;
      if (cfg_.test_full_scale_mm_s > 0.0f) {
        duty_l = clampf(rl / cfg_.test_full_scale_mm_s, -lim, lim);
        duty_r = clampf(rr / cfg_.test_full_scale_mm_s, -lim, lim);
      }
    }
  } else {
    ramp_left_.reset(0.0f);
    ramp_right_.reset(0.0f);
  }

  // Final clamp: whatever happened above, |duty| <= cap <= MOTOR_MAX_DUTY <= 1.
  duty_left_ = clampf(duty_l, -cap, cap);
  duty_right_ = clampf(duty_r, -cap, cap);
  if (std::isnan(duty_left_)) duty_left_ = 0.0f;
  if (std::isnan(duty_right_)) duty_right_ = 0.0f;
  if (motors_ok_) {
    if (enable != motors_enabled_) hw_.motors->setEnabled(enable);
    // Motor inversion (parameters) is applied here, at the output only: duty_left_/right_ stay
    // "positive = forward" for the encoder fault check and the tests.
    const float out_l = cfg_.motor_invert_left ? -duty_left_ : duty_left_;
    const float out_r = cfg_.motor_invert_right ? -duty_right_ : duty_right_;
    hw_.motors->setTrackDuty(enable ? out_l : 0.0f, enable ? out_r : 0.0f);
  }
  motors_enabled_ = enable;

  // The servo gets no pulse until the Pi sends the first SERVO (no movement at power-on).
  // During and after a bring-up jog the jog pulse holds until the next SERVO command.
  if (servo_ok_ && servo_.commanded() && !jog_hold_ && !cfg_.permanent_safe) {
    servo_.update(dt_s);
    last_servo_pulse_us_ = servo_.pulseUs();
    hw_.servo->writePulseUs(last_servo_pulse_us_);
  }
}

void FirmwareApp::controlTick(uint32_t now_ms) {
  if (!started_) return;
  float dt_s = static_cast<float>(now_ms - last_tick_ms_) / 1000.0f;
  if (dt_s <= 0.0f || dt_s > 0.1f) dt_s = static_cast<float>(cfg_.control_period_ms) / 1000.0f;
  last_tick_ms_ = now_ms;

  receive(now_ms);
  readSensors(now_ms, dt_s);
  stepGyroCalibration(now_ms);
  // The ramp outputs of the last period are the motion being applied right now.
  safety_.tick(now_ms, ultrasonic_.distancesMm(), ramp_left_.value(), ramp_right_.value());

  // Encoder fault: uses the duty applied during the last period.
  if (encoders_ok_) {
    for (int i = 0; i < 4; ++i) {
      const float duty = motors_enabled_ ? (i < 2 ? duty_left_ : duty_right_) : 0.0f;
      if (enc_fault_[i].update(now_ms, duty, enc_delta_[i])) {
        sendEvent(now_ms, proto::kEventEncoderFault, static_cast<int16_t>(i));
        // Closed loop cannot be trusted with a dead encoder. TEST mode only reports.
        if (safety_.mode() == proto::kModeDrive) safety_.forceStop();
      }
    }
  }

  driveOutputs(dt_s);
  checkSpeedSign(now_ms);

  SafetyEvent ev{};
  while (safety_.popEvent(&ev)) sendEvent(now_ms, ev.code, ev.arg);

  stepBringup(now_ms);
  stepParamSave();

  if (hw_.system != nullptr) {
    const uint8_t attempts = hw_.system->takeI2cRecovered();
    if (attempts > 0) sendEvent(now_ms, proto::kEventI2cRecovered, attempts);
  }
  if (diag_pending_ &&
      (hw_.system == nullptr || hw_.system->i2cScanDone() ||
       static_cast<int32_t>(now_ms - diag_deadline_ms_) >= 0)) {
    diag_pending_ = false;
    sendDiag(now_ms);
  }
}

uint16_t FirmwareApp::flags() const {
  uint16_t f = 0;
  if (motors_enabled_) f |= proto::kFlagMotorsEnabled;
  if (safety_.watchdogStop()) f |= proto::kFlagWatchdogStop;
  if (safety_.obstacleStop()) f |= proto::kFlagObstacleStop;
  if (battery_.low()) f |= proto::kFlagLowBattery;
  if (imu_ok_) f |= proto::kFlagImuOk;
  if (gyro_cal_.running()) f |= proto::kFlagGyroCalibrating;
  bool enc_fault = !encoders_ok_;
  for (int i = 0; i < 4; ++i) enc_fault = enc_fault || enc_fault_[i].faulty();
  if (enc_fault) f |= proto::kFlagEncoderFault;
  if (ultrasonic_.anyFault()) f |= proto::kFlagUltrasonicFault;
  if (power_ok_) f |= proto::kFlagPowerSensorOk;
  if (safety_.rearObstacleStop()) f |= proto::kFlagRearObstacleStop;
  if (params_.unsaved()) f |= proto::kFlagParamsUnsaved;
  if (bringupActive()) f |= proto::kFlagBringupActive;
  if (vcomp_.fallback()) f |= proto::kFlagVoltageCompFallback;
  // kFlagSimulated is never set here: this is the real firmware.
  return f;
}

void FirmwareApp::telemetryTick(uint32_t now_ms) {
  if (!started_) return;
  proto::Telemetry t{};
  t.t_ms = now_ms;
  for (int i = 0; i < 4; ++i) t.enc[i] = enc_[i];
  if (imu_ok_) {
    const float z = static_cast<float>(imu_.gyro_z_urad_s - gyro_cal_.biasUradS()) * cfg_.gyro_scale;
    t.gyro_z_urad_s = static_cast<int32_t>(clampf(z, -2.0e9f, 2.0e9f));
  }
  for (int i = 0; i < 3; ++i) t.accel_mg[i] = imu_ok_ ? imu_.accel_mg[i] : 0;
  const uint16_t* us = ultrasonic_.distancesMm();
  for (int i = 0; i < 4; ++i) t.us_mm[i] = us[i];
  t.battery_mv = power_ok_ ? battery_mv_ : 0;
  t.battery_ma = power_ok_ ? battery_ma_ : 0;
  t.flags = flags();
  t.param_hash = params_.hash();
  send(proto::makeTelemetry(t));
}

}  // namespace scr
