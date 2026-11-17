#include "scr_app.h"

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
}  // namespace

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
      ultrasonic_(config.ultrasonic, hw.ultrasonic) {}

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

  safety_.begin(now_ms);
  last_tick_ms_ = now_ms;
  last_power_ms_ = now_ms;
  last_enc_change_ms_ = now_ms;
  last_error_report_ms_ = now_ms;
  started_ = true;
  sendEvent(now_ms, proto::kEventBoot, 0);
  if (!imu_present_) sendEvent(now_ms, proto::kEventImuFault, 0);
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

bool FirmwareApp::moving(uint32_t now_ms) const {
  if (safety_.targetLeft() != 0 || safety_.targetRight() != 0) return true;
  if (ramp_left_.value() != 0.0f || ramp_right_.value() != 0.0f) return true;
  if (have_enc_ && now_ms - last_enc_change_ms_ < cfg_.still_ms) return true;
  return false;
}

void FirmwareApp::handleFrame(uint32_t now_ms, const proto::Message& msg, uint8_t seq) {
  const uint8_t type = static_cast<uint8_t>(msg.type);
  switch (msg.type) {
    case proto::MsgType::Telemetry:
    case proto::MsgType::Event:
    case proto::MsgType::Ack:
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
      if (servo_ok_) {
        servo_.setTarget(msg.servo.pan_cdeg);
      } else {
        sendAck(type, seq, proto::kAckRejectedSafety);  // no usable servo output
      }
      break;
    case proto::MsgType::Mode: {
      const bool closed_loop_ready = encoders_ok_ && cfg_.metres_per_tick > 0.0f;
      sendAck(type, seq, safety_.onMode(msg.mode.mode, motors_ok_, closed_loop_ready));
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
    default:
      break;
  }
}

// ---- sensors --------------------------------------------------------------------------------
void FirmwareApp::readSensors(uint32_t now_ms, float dt_s) {
  enc_changed_this_tick_ = false;
  if (encoders_ok_) {
    int32_t raw[4] = {0, 0, 0, 0};
    hw_.encoders->read(raw);
    for (int i = 0; i < 4; ++i) {
      const int32_t v = cfg_.encoder_invert[i] ? -raw[i] : raw[i];
      enc_delta_[i] = have_enc_ ? v - enc_[i] : 0;
      enc_[i] = v;
      if (enc_delta_[i] != 0) enc_changed_this_tick_ = true;
    }
    have_enc_ = true;
    if (enc_changed_this_tick_) last_enc_change_ms_ = now_ms;
    // Track speed = mean of the front and the rear encoder of that side (docs/ODOMETRY.md).
    const float k = cfg_.metres_per_tick / dt_s;
    const float left = 0.5f * static_cast<float>(enc_delta_[0] + enc_delta_[1]) * k;
    const float right = 0.5f * static_cast<float>(enc_delta_[2] + enc_delta_[3]) * k;
    const float a = clampf(cfg_.speed_filter_alpha, 0.0f, 1.0f);
    speed_left_m_s_ += a * (left - speed_left_m_s_);
    speed_right_m_s_ += a * (right - speed_right_m_s_);
  }

  if (imu_present_) {
    hal::ImuSample s{};
    const bool ok = hw_.imu->read(&s);
    if (ok) {
      imu_ = s;
    } else if (imu_ok_) {
      sendEvent(now_ms, proto::kEventImuFault, 0);  // once per failure
    }
    imu_ok_ = ok;
  }

  ultrasonic_.tick(now_ms);

  if (power_present_ && now_ms - last_power_ms_ >= cfg_.power_period_ms) {
    last_power_ms_ = now_ms;
    uint16_t mv = 0;
    int16_t ma = 0;
    power_ok_ = hw_.power->read(&mv, &ma);
    if (power_ok_) {
      battery_mv_ = mv;
      battery_ma_ = ma;
    }
    if (battery_.update(now_ms, battery_mv_, power_ok_)) {
      sendEvent(now_ms, proto::kEventLowBattery, saturateI16(battery_mv_));
    }
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
  const bool enable = motors_ok_ && mode != proto::kModeSafe;
  float duty_l = 0.0f;
  float duty_r = 0.0f;

  if (enable) {
    const int16_t tl = clampI16(safety_.targetLeft(), cfg_.max_speed_mm_s);
    const int16_t tr = clampI16(safety_.targetRight(), cfg_.max_speed_mm_s);
    const float rl = ramp_left_.update(static_cast<float>(tl), dt_s);
    const float rr = ramp_right_.update(static_cast<float>(tr), dt_s);
    if (mode == proto::kModeDrive) {
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
    } else {  // TEST: open loop
      const float lim = clampf(cfg_.test_max_duty, 0.0f, 1.0f);
      if (cfg_.test_full_scale_mm_s > 0.0f) {
        duty_l = clampf(rl / cfg_.test_full_scale_mm_s, -lim, lim);
        duty_r = clampf(rr / cfg_.test_full_scale_mm_s, -lim, lim);
      }
    }
  } else {
    ramp_left_.reset(0.0f);
    ramp_right_.reset(0.0f);
  }

  duty_left_ = clampf(duty_l, -1.0f, 1.0f);
  duty_right_ = clampf(duty_r, -1.0f, 1.0f);
  if (motors_ok_) {
    if (enable != motors_enabled_) hw_.motors->setEnabled(enable);
    hw_.motors->setTrackDuty(enable ? duty_left_ : 0.0f, enable ? duty_right_ : 0.0f);
  }
  motors_enabled_ = enable;

  // The servo gets no pulse until the Pi sends the first SERVO (no movement at power-on).
  if (servo_ok_ && servo_.commanded()) {
    servo_.update(dt_s);
    hw_.servo->writePulseUs(servo_.pulseUs());
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
  safety_.tick(now_ms, ultrasonic_.distancesMm());

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

  SafetyEvent ev{};
  while (safety_.popEvent(&ev)) sendEvent(now_ms, ev.code, ev.arg);
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
  // kFlagSimulated is never set here: this is the real firmware.
  return f;
}

void FirmwareApp::telemetryTick(uint32_t now_ms) {
  if (!started_) return;
  proto::Telemetry t{};
  t.t_ms = now_ms;
  for (int i = 0; i < 4; ++i) t.enc[i] = enc_[i];
  t.gyro_z_urad_s = imu_ok_ ? imu_.gyro_z_urad_s - gyro_cal_.biasUradS() : 0;
  for (int i = 0; i < 3; ++i) t.accel_mg[i] = imu_ok_ ? imu_.accel_mg[i] : 0;
  const uint16_t* us = ultrasonic_.distancesMm();
  for (int i = 0; i < 4; ++i) t.us_mm[i] = us[i];
  t.battery_mv = power_ok_ ? battery_mv_ : 0;
  t.battery_ma = power_ok_ ? battery_ma_ : 0;
  t.flags = flags();
  send(proto::makeTelemetry(t));
}

}  // namespace scr
