// motors, encoders, servo, link, parameter storage. NOT VERIFIED ON HARDWARE (never run on an
// ESP32; compiled for Arduino-ESP32 core 2.x and 3.x only).
#include <Preferences.h>

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
#define SCR_IDF5_PCNT 1
#include <driver/pulse_cnt.h>
#else
#include <driver/pcnt.h>
#endif

#include <cstdio>

#include "esp32_drivers.h"
#include "scr_control.h"

namespace scr {
namespace esp32 {

namespace {

// The LEDC API changed between Arduino-ESP32 core 2.x and 3.x.
bool pwmAttach(int pin, uint8_t channel, uint32_t hz, uint8_t bits) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttachChannel(static_cast<uint8_t>(pin), hz, bits, channel);
#else
  if (ledcSetup(channel, hz, bits) == 0) return false;
  ledcAttachPin(static_cast<uint8_t>(pin), channel);
  return true;
#endif
}

void pwmWrite(int pin, uint8_t channel, uint32_t duty) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)channel;
  ledcWrite(static_cast<uint8_t>(pin), duty);
#else
  (void)pin;
  ledcWrite(channel, duty);
#endif
}

float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

}  // namespace

// ---- boot: motor outputs LOW before anything else -------------------------------------------
void motorPinsLowAtBoot() {
#if !defined(PINS_COMPILE_CHECK_ONLY) && !defined(SCR_CI_PINS_DO_NOT_FLASH)
  const int motor_pins[6] = {PIN_MOTOR_LEFT_PWM,  PIN_MOTOR_LEFT_DIR_A,  PIN_MOTOR_LEFT_DIR_B,
                             PIN_MOTOR_RIGHT_PWM, PIN_MOTOR_RIGHT_DIR_A, PIN_MOTOR_RIGHT_DIR_B};
  for (int pin : motor_pins) {
    if (!pins::isGpio(pin)) continue;
    digitalWrite(static_cast<uint8_t>(pin), LOW);  // output latch low first, then output mode
    pinMode(static_cast<uint8_t>(pin), OUTPUT);
    digitalWrite(static_cast<uint8_t>(pin), LOW);
  }
#endif
}

// ---- motors ---------------------------------------------------------------------------------
bool Esp32Motors::begin() {
  ok_ = false;
  enabled_ = false;
  if (!pins::allAssigned({PIN_MOTOR_LEFT_PWM, PIN_MOTOR_LEFT_DIR_A, PIN_MOTOR_LEFT_DIR_B,
                          PIN_MOTOR_RIGHT_PWM, PIN_MOTOR_RIGHT_DIR_A, PIN_MOTOR_RIGHT_DIR_B})) {
    return false;
  }
  const int dirs[4] = {PIN_MOTOR_LEFT_DIR_A, PIN_MOTOR_LEFT_DIR_B, PIN_MOTOR_RIGHT_DIR_A,
                       PIN_MOTOR_RIGHT_DIR_B};
  for (int pin : dirs) {
    pinMode(static_cast<uint8_t>(pin), OUTPUT);
    digitalWrite(static_cast<uint8_t>(pin), LOW);
  }
  if (!pwmAttach(PIN_MOTOR_LEFT_PWM, kLedcChannelMotorLeft, cfg::MOTOR_PWM_HZ, cfg::MOTOR_PWM_BITS)) return false;
  if (!pwmAttach(PIN_MOTOR_RIGHT_PWM, kLedcChannelMotorRight, cfg::MOTOR_PWM_HZ, cfg::MOTOR_PWM_BITS)) return false;
  pwmWrite(PIN_MOTOR_LEFT_PWM, kLedcChannelMotorLeft, 0);
  pwmWrite(PIN_MOTOR_RIGHT_PWM, kLedcChannelMotorRight, 0);
  ok_ = true;
  return true;
}

void Esp32Motors::writeSide(int pwm_pin, uint8_t channel, int dir_a, int dir_b, float duty) {
  // Both drivers: PWM on the enable/PWM input, direction on the two inputs.
  // Zero duty: both direction inputs low and PWM 0 (L298N: coast; VNH: brake to ground).
  // The duty caps (MOTOR_MAX_DUTY, voltage compensation, TEST_MAX_DUTY) are applied by the
  // application before this call; here only the physical range [-1, 1].
  duty = clampf(duty, -1.0f, 1.0f);
  if (duty != duty) duty = 0.0f;  // NaN
  const uint32_t full = (1u << cfg::MOTOR_PWM_BITS) - 1u;
  const float mag = duty < 0.0f ? -duty : duty;
  const uint32_t counts = static_cast<uint32_t>(mag * static_cast<float>(full) + 0.5f);
  if (counts == 0) {
    pwmWrite(pwm_pin, channel, 0);
    digitalWrite(static_cast<uint8_t>(dir_a), LOW);
    digitalWrite(static_cast<uint8_t>(dir_b), LOW);
    return;
  }
  digitalWrite(static_cast<uint8_t>(dir_a), duty > 0.0f ? HIGH : LOW);
  digitalWrite(static_cast<uint8_t>(dir_b), duty > 0.0f ? LOW : HIGH);
  pwmWrite(pwm_pin, channel, counts);
}

void Esp32Motors::setEnabled(bool enabled) {
  if (!ok_) return;
  enabled_ = enabled;
  if (!enabled) setTrackDuty(0.0f, 0.0f);
}

void Esp32Motors::setTrackDuty(float left, float right) {
  if (!ok_) return;  // never touches a pin that is not assigned
  if (!enabled_) {
    left = 0.0f;
    right = 0.0f;
  }
  writeSide(PIN_MOTOR_LEFT_PWM, kLedcChannelMotorLeft, PIN_MOTOR_LEFT_DIR_A, PIN_MOTOR_LEFT_DIR_B, left);
  writeSide(PIN_MOTOR_RIGHT_PWM, kLedcChannelMotorRight, PIN_MOTOR_RIGHT_DIR_A, PIN_MOTOR_RIGHT_DIR_B, right);
}

// ---- encoders -------------------------------------------------------------------------------
namespace {
// The hardware counter is 16 bit and resets to zero when it reaches +-limit, so the counts are
// read as differences on a circle of `limit` values (scr::pcntDelta) and summed in 32 bit.
constexpr int16_t kPcntLimit = 32767;
#if defined(SCR_IDF5_PCNT)
pcnt_unit_handle_t g_units[4] = {nullptr, nullptr, nullptr, nullptr};
#else
const pcnt_unit_t kUnits[4] = {PCNT_UNIT_0, PCNT_UNIT_1, PCNT_UNIT_2, PCNT_UNIT_3};
#endif
}  // namespace

bool Esp32Encoders::begin() {
  ok_ = false;
  const int a[4] = {PIN_ENC_FL_A, PIN_ENC_RL_A, PIN_ENC_FR_A, PIN_ENC_RR_A};
  const int b[4] = {PIN_ENC_FL_B, PIN_ENC_RL_B, PIN_ENC_FR_B, PIN_ENC_RR_B};
  if (!pins::allAssigned({a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3]})) return false;
  for (int i = 0; i < 4; ++i) {
#if defined(SCR_IDF5_PCNT)
    // ESP-IDF 5 pulse counter driver (Arduino-ESP32 core 3.x).
    pcnt_unit_config_t uc = {};
    uc.low_limit = -kPcntLimit;
    uc.high_limit = kPcntLimit;
    if (pcnt_new_unit(&uc, &g_units[i]) != ESP_OK) return false;
    if (cfg::ENCODER_GLITCH_FILTER > 0) {
      pcnt_glitch_filter_config_t fc = {};
      fc.max_glitch_ns = static_cast<uint32_t>(cfg::ENCODER_GLITCH_FILTER) * 25u / 2u;  // APB 80 MHz: 12.5 ns per cycle
      if (pcnt_unit_set_glitch_filter(g_units[i], &fc) != ESP_OK) return false;
    }
    pcnt_chan_config_t ca = {};
    ca.edge_gpio_num = a[i];
    ca.level_gpio_num = b[i];
    pcnt_channel_handle_t chan_a = nullptr;
    if (pcnt_new_channel(g_units[i], &ca, &chan_a) != ESP_OK) return false;
    pcnt_chan_config_t cb = {};
    cb.edge_gpio_num = b[i];
    cb.level_gpio_num = a[i];
    pcnt_channel_handle_t chan_b = nullptr;
    if (pcnt_new_channel(g_units[i], &cb, &chan_b) != ESP_OK) return false;
    // Same counting as the core 2.x branch: A edges counted with direction from B, B edges with
    // direction from A; the direction is inverted while the control input is low.
    pcnt_channel_set_edge_action(chan_a, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
    pcnt_channel_set_level_action(chan_a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_channel_set_edge_action(chan_b, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(chan_b, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    if (pcnt_unit_enable(g_units[i]) != ESP_OK) return false;
    pcnt_unit_clear_count(g_units[i]);
    if (pcnt_unit_start(g_units[i]) != ESP_OK) return false;
#else
    pcnt_config_t c = {};
    c.unit = kUnits[i];
    c.counter_h_lim = kPcntLimit;
    c.counter_l_lim = -kPcntLimit;
    // channel 0: edges of A, direction from B
    c.channel = PCNT_CHANNEL_0;
    c.pulse_gpio_num = a[i];
    c.ctrl_gpio_num = b[i];
    c.pos_mode = PCNT_COUNT_DEC;
    c.neg_mode = PCNT_COUNT_INC;
    c.lctrl_mode = PCNT_MODE_REVERSE;
    c.hctrl_mode = PCNT_MODE_KEEP;
    if (pcnt_unit_config(&c) != ESP_OK) return false;
    // channel 1: edges of B, direction from A
    c.channel = PCNT_CHANNEL_1;
    c.pulse_gpio_num = b[i];
    c.ctrl_gpio_num = a[i];
    c.pos_mode = PCNT_COUNT_INC;
    c.neg_mode = PCNT_COUNT_DEC;
    if (pcnt_unit_config(&c) != ESP_OK) return false;
    if (cfg::ENCODER_GLITCH_FILTER > 0) {
      pcnt_set_filter_value(kUnits[i], cfg::ENCODER_GLITCH_FILTER);
      pcnt_filter_enable(kUnits[i]);
    } else {
      pcnt_filter_disable(kUnits[i]);
    }
    pcnt_counter_pause(kUnits[i]);
    pcnt_counter_clear(kUnits[i]);
    pcnt_counter_resume(kUnits[i]);
#endif
    last_[i] = 0;
    total_[i] = 0;
  }
  ok_ = true;
  return true;
}

void Esp32Encoders::read(int32_t counts[4]) {
  for (int i = 0; i < 4; ++i) {
    if (ok_) {
      int16_t now = 0;
#if defined(SCR_IDF5_PCNT)
      int value = 0;
      pcnt_unit_get_count(g_units[i], &value);
      now = static_cast<int16_t>(value);
#else
      pcnt_get_counter_value(kUnits[i], &now);
#endif
      total_[i] = wrapAdd(total_[i], pcntDelta(last_[i], now, kPcntLimit));
      last_[i] = now;
    }
    counts[i] = total_[i];
  }
}

// ---- servo ----------------------------------------------------------------------------------
namespace {
constexpr uint32_t kServoHz = 50;
constexpr uint8_t kServoBits = 16;
}  // namespace

bool Esp32Servo::begin() {
  ok_ = false;
  if (!pins::allAssigned({PIN_SERVO_PAN})) return false;
  if (!pwmAttach(PIN_SERVO_PAN, kLedcChannelServo, kServoHz, kServoBits)) return false;
  pwmWrite(PIN_SERVO_PAN, kLedcChannelServo, 0);  // no pulses until the first command
  ok_ = true;
  return true;
}

void Esp32Servo::writePulseUs(uint16_t pulse_us) {
  if (!ok_) return;
  if (pulse_us > 2500) pulse_us = 2500;  // the compiled hard range of the protocol
  // period 20000 us at 50 Hz
  const uint32_t duty = (static_cast<uint32_t>(pulse_us) * ((1u << kServoBits) - 1u)) / 20000u;
  pwmWrite(PIN_SERVO_PAN, kLedcChannelServo, duty);
}

// ---- link -----------------------------------------------------------------------------------
void Esp32Link::begin() {
  Serial.setRxBufferSize(1024);
  Serial.begin(proto::kBaudRate);
}

size_t Esp32Link::read(uint8_t* buf, size_t cap) {
  size_t n = 0;
  while (n < cap && Serial.available() > 0) {
    const int c = Serial.read();
    if (c < 0) break;
    buf[n++] = static_cast<uint8_t>(c);
  }
  return n;
}

void Esp32Link::write(const uint8_t* data, size_t len) { Serial.write(data, len); }

// ---- parameter storage (NVS) ----------------------------------------------------------------
namespace {
Preferences g_prefs;
void keyOf(uint16_t id, char* key) { std::snprintf(key, 8, "p%u", static_cast<unsigned>(id)); }
}  // namespace

bool Esp32Storage::begin() {
  ok_ = g_prefs.begin("scr-params", false);
  return ok_;
}

bool Esp32Storage::read(uint16_t id, float* value) {
  if (!ok_) return false;
  char key[8];
  keyOf(id, key);
  if (!g_prefs.isKey(key)) return false;
  *value = g_prefs.getFloat(key, 0.0f);
  return true;
}

bool Esp32Storage::write(uint16_t id, float value) {
  if (!ok_) return false;
  char key[8];
  keyOf(id, key);
  return g_prefs.putFloat(key, value) == sizeof(float);  // Preferences commits each put
}

bool Esp32Storage::commit() { return ok_; }

}  // namespace esp32
}  // namespace scr
