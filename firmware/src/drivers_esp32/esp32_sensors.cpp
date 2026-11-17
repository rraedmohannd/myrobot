// I2C task (IMU ICM-20948, power INA219, MCP23017 trigger expander, scan, bus recovery),
// ultrasonic (HC-SR04 x4) and system services. NOT VERIFIED ON HARDWARE: never run on an ESP32.
// Register addresses and scale factors are taken from the parts' datasheets and have not been
// checked against real devices.
//
// Robustness (S2.4): no I2C transfer ever runs on the control task. The I2C task owns the bus;
// the control task only copies its latest results under a spinlock (a few dozen bytes). A stuck
// device therefore makes the IMU / INA219 values stale (IMU fault, voltage compensation fallback)
// but cannot stall the 100 Hz control loop. After I2C_FAILS_BEFORE_RECOVERY failed transfers in a
// row, the task clears the bus (9 SCL clocks + STOP) and reports EVENT I2C_RECOVERED.
#include <Wire.h>
#include <esp_system.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "esp32_drivers.h"

namespace scr {
namespace esp32 {

namespace {

// ---- shared state between the I2C task and the other tasks ---------------------------------
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t g_i2c_task = nullptr;
bool g_bus_ok = false;

struct Shared {
  hal::ImuSample imu{};
  uint32_t imu_ms = 0;
  bool imu_valid = false;
  uint16_t bus_mv = 0;
  int32_t shunt_uv = 0;
  uint32_t power_ms = 0;
  bool power_valid = false;
  uint8_t scan[8] = {0};
  uint8_t scan_count = 0;
  bool scan_requested = false;
  bool scan_done = false;
  uint8_t recovered_attempts = 0;  // reported once, then 0
  uint8_t trigger_mask = 0;        // expander trigger requests (bit = ultrasonic index)
  // control loop statistics (us), written by the control task
  uint32_t period_min_us = 0xFFFFFFFFu;
  uint32_t period_max_us = 0;
};
Shared g;

bool g_imu_ok = false;      // begin() succeeded
bool g_power_ok = false;
bool g_expander_ok = false;
uint8_t g_expander_trig_bit[4] = {0xFF, 0xFF, 0xFF, 0xFF};  // MCP23017 pin per ultrasonic, 0xFF = GPIO
uint16_t g_expander_outputs = 0;  // OLAT shadow (all LOW)

constexpr uint32_t kImuFreshMs = 50;
constexpr uint32_t kPowerFreshMs = 500;
constexpr uint32_t kScanBudgetMs = 50;   // a scan never blocks the I2C task longer than this
constexpr uint32_t kRecoveryMinGapMs = 1000;

// ---- raw bus access (I2C task and setup() only) ----------------------------------------------
uint8_t g_fails = 0;  // consecutive failed transfers

bool noteResult(bool ok) {
  if (ok) {
    g_fails = 0;
  } else if (g_fails < 255) {
    ++g_fails;
  }
  return ok;
}

bool writeReg(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return noteResult(Wire.endTransmission() == 0);
}

bool readRegs(uint8_t address, uint8_t reg, uint8_t* out, size_t len) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return noteResult(false);
  if (Wire.requestFrom(static_cast<int>(address), static_cast<int>(len)) != static_cast<int>(len)) {
    return noteResult(false);
  }
  for (size_t i = 0; i < len; ++i) out[i] = static_cast<uint8_t>(Wire.read());
  return noteResult(true);
}

// Bus clear (NXP UM10204 section 3.1.16): a slave that holds SDA low is clocked out with up to 9
// SCL pulses, then a STOP condition is generated. Returns the number of attempts (1..3).
uint8_t busClear() {
  Wire.end();
  uint8_t attempts = 0;
  for (attempts = 1; attempts <= 3; ++attempts) {
    pinMode(static_cast<uint8_t>(PIN_I2C_SDA), INPUT_PULLUP);
    pinMode(static_cast<uint8_t>(PIN_I2C_SCL), OUTPUT_OPEN_DRAIN);
    for (int i = 0; i < 9 && digitalRead(static_cast<uint8_t>(PIN_I2C_SDA)) == LOW; ++i) {
      digitalWrite(static_cast<uint8_t>(PIN_I2C_SCL), LOW);
      delayMicroseconds(5);
      digitalWrite(static_cast<uint8_t>(PIN_I2C_SCL), HIGH);
      delayMicroseconds(5);
    }
    // STOP: SDA low -> high while SCL is high
    pinMode(static_cast<uint8_t>(PIN_I2C_SDA), OUTPUT_OPEN_DRAIN);
    digitalWrite(static_cast<uint8_t>(PIN_I2C_SDA), LOW);
    delayMicroseconds(5);
    digitalWrite(static_cast<uint8_t>(PIN_I2C_SCL), HIGH);
    delayMicroseconds(5);
    digitalWrite(static_cast<uint8_t>(PIN_I2C_SDA), HIGH);
    delayMicroseconds(5);
    pinMode(static_cast<uint8_t>(PIN_I2C_SDA), INPUT_PULLUP);
    if (digitalRead(static_cast<uint8_t>(PIN_I2C_SDA)) == HIGH) break;
  }
  if (attempts > 3) attempts = 3;
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, cfg::I2C_HZ);
  Wire.setTimeOut(cfg::I2C_TIMEOUT_MS);
  g_fails = 0;
  return attempts;
}

// ---- ICM-20948 -------------------------------------------------------------------------------
constexpr uint8_t kRegBankSel = 0x7F;
constexpr uint8_t kRegWhoAmI = 0x00;  // bank 0
constexpr uint8_t kWhoAmIValue = 0xEA;
constexpr uint8_t kRegPwrMgmt1 = 0x06;
constexpr uint8_t kRegPwrMgmt2 = 0x07;
constexpr uint8_t kRegAccelXoutH = 0x2D;  // accel x,y,z then gyro x,y,z: 12 bytes, big-endian
constexpr uint8_t kRegGyroConfig1 = 0x01;  // bank 2
constexpr uint8_t kRegAccelConfig = 0x14;
// Full scale +-500 deg/s (65.5 LSB per deg/s) and +-4 g (8192 LSB per g), low-pass filter on.
constexpr uint8_t kGyroConfigValue = (3u << 3) | (1u << 1) | 1u;
constexpr uint8_t kAccelConfigValue = (3u << 3) | (1u << 1) | 1u;
constexpr float kUradPerGyroLsb = (1.0f / 65.5f) * 17453.2925f;  // deg/s -> micro-rad/s
constexpr float kMgPerAccelLsb = 1000.0f / 8192.0f;

bool selectBank(uint8_t bank) {
  return writeReg(cfg::IMU_I2C_ADDRESS, kRegBankSel, static_cast<uint8_t>(bank << 4));
}

bool imuSample(hal::ImuSample* out) {
  uint8_t raw[12];
  if (!readRegs(cfg::IMU_I2C_ADDRESS, kRegAccelXoutH, raw, sizeof(raw))) return false;
  int16_t accel[3];
  int16_t gyro[3];
  for (int i = 0; i < 3; ++i) {
    accel[i] = static_cast<int16_t>((static_cast<uint16_t>(raw[2 * i]) << 8) | raw[2 * i + 1]);
    gyro[i] = static_cast<int16_t>((static_cast<uint16_t>(raw[6 + 2 * i]) << 8) | raw[7 + 2 * i]);
  }
  // Map the chip axes to the robot axes (x forward, y left, z up) as configured.
  const uint8_t axis[3] = {cfg::IMU_AXIS_FORWARD, cfg::IMU_AXIS_LEFT, cfg::IMU_AXIS_UP};
  const int8_t sign[3] = {cfg::IMU_SIGN_FORWARD, cfg::IMU_SIGN_LEFT, cfg::IMU_SIGN_UP};
  for (int i = 0; i < 3; ++i) {
    const float mg = static_cast<float>(accel[axis[i] % 3]) * kMgPerAccelLsb * static_cast<float>(sign[i]);
    out->accel_mg[i] = static_cast<int16_t>(mg);
    out->gyro_urad_s[i] = static_cast<int32_t>(static_cast<float>(gyro[axis[i] % 3]) * kUradPerGyroLsb *
                                               static_cast<float>(sign[i]));
  }
  out->gyro_z_urad_s = out->gyro_urad_s[2];  // rotation about "up", counter-clockwise positive
  return true;
}

// ---- INA219 ----------------------------------------------------------------------------------
constexpr uint8_t kInaRegShunt = 0x01;  // signed, 10 uV per LSB
constexpr uint8_t kInaRegBus = 0x02;    // bits 15..3, 4 mV per LSB

bool powerSample(uint16_t* bus_mv, int32_t* shunt_uv) {
  uint8_t b[2];
  uint8_t s[2];
  if (!readRegs(cfg::INA219_I2C_ADDRESS, kInaRegBus, b, 2)) return false;
  if (!readRegs(cfg::INA219_I2C_ADDRESS, kInaRegShunt, s, 2)) return false;
  const uint16_t bus_raw = static_cast<uint16_t>((static_cast<uint16_t>(b[0]) << 8) | b[1]);
  const int16_t shunt_raw = static_cast<int16_t>((static_cast<uint16_t>(s[0]) << 8) | s[1]);
  *bus_mv = static_cast<uint16_t>((bus_raw >> 3) * 4u);
  *shunt_uv = static_cast<int32_t>(shunt_raw) * 10;
  return true;
}

// ---- MCP23017 (pin plan option B: HC-SR04 trigger lines) -------------------------------------
constexpr uint8_t kMcpIodirA = 0x00;  // BANK = 0 (power-on default)
constexpr uint8_t kMcpIodirB = 0x01;
constexpr uint8_t kMcpOlatA = 0x14;
constexpr uint8_t kMcpOlatB = 0x15;

bool expanderWriteOutputs(uint16_t outputs) {
  const bool a = writeReg(cfg::EXPANDER_I2C_ADDRESS, kMcpOlatA, static_cast<uint8_t>(outputs & 0xFF));
  const bool b = writeReg(cfg::EXPANDER_I2C_ADDRESS, kMcpOlatB, static_cast<uint8_t>(outputs >> 8));
  return a && b;
}

// Outputs LOW first (latch), then only the trigger pins become outputs; all others stay inputs.
bool expanderBegin() {
  uint16_t dir = 0xFFFF;  // 1 = input
  bool any = false;
  for (int i = 0; i < 4; ++i) {
    if (g_expander_trig_bit[i] < 16) {
      dir = static_cast<uint16_t>(dir & ~(1u << g_expander_trig_bit[i]));
      any = true;
    }
  }
  if (!any) return false;
  g_expander_outputs = 0;
  if (!expanderWriteOutputs(0)) return false;
  if (!writeReg(cfg::EXPANDER_I2C_ADDRESS, kMcpIodirA, static_cast<uint8_t>(dir & 0xFF))) return false;
  if (!writeReg(cfg::EXPANDER_I2C_ADDRESS, kMcpIodirB, static_cast<uint8_t>(dir >> 8))) return false;
  return true;
}

// One trigger pulse over I2C: high, at least 10 us (one register write alone takes ~75 us at
// 400 kHz, ~300 us at 100 kHz), low. The echo is timed from its own GPIO edges.
void expanderPulse(uint8_t bit) {
  const uint8_t reg = bit < 8 ? kMcpOlatA : kMcpOlatB;
  const uint8_t mask = static_cast<uint8_t>(1u << (bit & 7));
  const uint8_t base = static_cast<uint8_t>(bit < 8 ? (g_expander_outputs & 0xFF) : (g_expander_outputs >> 8));
  writeReg(cfg::EXPANDER_I2C_ADDRESS, reg, static_cast<uint8_t>(base | mask));
  delayMicroseconds(10);
  writeReg(cfg::EXPANDER_I2C_ADDRESS, reg, base);
}

// ---- the scan (bounded) ----------------------------------------------------------------------
void scanBus() {
  uint8_t found[8] = {0};
  uint8_t n = 0;
  const uint32_t start = millis();
  for (uint8_t a = cfg::I2C_SCAN_FIRST; a <= cfg::I2C_SCAN_LAST; ++a) {
    if (millis() - start > kScanBudgetMs) break;  // a hung bus must not stall the task
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0 && n < 8) found[n++] = a;
  }
  portENTER_CRITICAL(&g_mux);
  for (int i = 0; i < 8; ++i) g.scan[i] = found[i];
  g.scan_count = n;
  g.scan_done = true;
  portEXIT_CRITICAL(&g_mux);
}

void i2cTask(void*) {
  TickType_t last = xTaskGetTickCount();
  uint32_t cycle = 0;
  uint32_t last_recovery_ms = 0;
  for (;;) {
    // 1. Trigger requests (woken at once by Esp32Ultrasonic::trigger through a notification).
    portENTER_CRITICAL(&g_mux);
    const uint8_t trig = g.trigger_mask;
    g.trigger_mask = 0;
    const bool scan = g.scan_requested;
    g.scan_requested = false;
    portEXIT_CRITICAL(&g_mux);
    for (int i = 0; i < 4; ++i) {
      if ((trig & (1u << i)) && g_expander_ok && g_expander_trig_bit[i] < 16) expanderPulse(g_expander_trig_bit[i]);
    }
    // 2. Scan on request (boot, DIAG_REQUEST).
    if (scan) scanBus();
    // 3. IMU every cycle, INA219 every 10th.
    if (g_imu_ok) {
      hal::ImuSample s{};
      if (imuSample(&s)) {
        portENTER_CRITICAL(&g_mux);
        g.imu = s;
        g.imu_ms = millis();
        g.imu_valid = true;
        portEXIT_CRITICAL(&g_mux);
      }
    }
    if (g_power_ok && cycle % 10 == 0) {
      uint16_t mv = 0;
      int32_t uv = 0;
      if (powerSample(&mv, &uv)) {
        portENTER_CRITICAL(&g_mux);
        g.bus_mv = mv;
        g.shunt_uv = uv;
        g.power_ms = millis();
        g.power_valid = true;
        portEXIT_CRITICAL(&g_mux);
      }
    }
    // 4. Bus recovery after repeated failures (at most once a second).
    if (g_fails >= cfg::I2C_FAILS_BEFORE_RECOVERY && millis() - last_recovery_ms >= kRecoveryMinGapMs) {
      last_recovery_ms = millis();
      const uint8_t attempts = busClear();
      if (g_expander_ok) expanderBegin();  // the expander may have lost its state
      portENTER_CRITICAL(&g_mux);
      g.recovered_attempts = attempts;
      portEXIT_CRITICAL(&g_mux);
    }
    ++cycle;
    // Sleep until the next period, or until a trigger request wakes the task early.
    const TickType_t period = pdMS_TO_TICKS(cfg::I2C_TASK_PERIOD_MS);
    const TickType_t now = xTaskGetTickCount();
    const TickType_t elapsed = now - last;
    if (elapsed < period) ulTaskNotifyTake(pdTRUE, period - elapsed);
    if (xTaskGetTickCount() - last >= period) last = xTaskGetTickCount();
  }
}

}  // namespace

// ---- bus start and task ---------------------------------------------------------------------
bool i2cBusBegin() {
  static bool started = false;
  if (started) return g_bus_ok;
  started = true;
  if (!pins::allAssigned({PIN_I2C_SDA, PIN_I2C_SCL})) return false;
  g_bus_ok = Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, cfg::I2C_HZ);
  if (g_bus_ok) Wire.setTimeOut(cfg::I2C_TIMEOUT_MS);  // ms: one transfer can never block long
  return g_bus_ok;
}

void i2cTaskStart() {
  if (!g_bus_ok || g_i2c_task != nullptr) return;
  // The expander (option B) is set up here, before the task owns the bus.
  if (g_expander_trig_bit[0] < 16 || g_expander_trig_bit[1] < 16 || g_expander_trig_bit[2] < 16 ||
      g_expander_trig_bit[3] < 16) {
    g_expander_ok = expanderBegin();
  }
  portENTER_CRITICAL(&g_mux);
  g.scan_requested = true;  // boot scan
  g.scan_done = false;
  portEXIT_CRITICAL(&g_mux);
  // Below the control (5) and telemetry (4) tasks; not on the task watchdog: a hung I2C task
  // only makes the IMU/INA219 values stale, which the control task handles.
  xTaskCreatePinnedToCore(i2cTask, "i2c", 4096, nullptr, 3, &g_i2c_task, 0);
}

void recordControlPeriodUs(uint32_t period_us) {
  portENTER_CRITICAL(&g_mux);
  if (period_us < g.period_min_us) g.period_min_us = period_us;
  if (period_us > g.period_max_us) g.period_max_us = period_us;
  portEXIT_CRITICAL(&g_mux);
}

// ---- imu --------------------------------------------------------------------------------------
bool Esp32Imu::begin() {
  g_imu_ok = false;
  if (!i2cBusBegin()) return false;
  const uint8_t addr = cfg::IMU_I2C_ADDRESS;
  uint8_t who = 0;
  if (!selectBank(0) || !readRegs(addr, kRegWhoAmI, &who, 1) || who != kWhoAmIValue) return false;
  if (!writeReg(addr, kRegPwrMgmt1, 0x80)) return false;  // device reset
  delay(100);
  if (!selectBank(0)) return false;
  if (!writeReg(addr, kRegPwrMgmt1, 0x01)) return false;  // wake, best clock source
  delay(20);
  if (!writeReg(addr, kRegPwrMgmt2, 0x00)) return false;  // accel + gyro on
  if (!selectBank(2)) return false;
  if (!writeReg(addr, kRegGyroConfig1, kGyroConfigValue)) return false;
  if (!writeReg(addr, kRegAccelConfig, kAccelConfigValue)) return false;
  if (!selectBank(0)) return false;
  g_imu_ok = true;
  return true;
}

bool Esp32Imu::read(hal::ImuSample* out) {
  if (!g_imu_ok) return false;
  portENTER_CRITICAL(&g_mux);
  const bool fresh = g.imu_valid && millis() - g.imu_ms <= kImuFreshMs;
  if (fresh) *out = g.imu;
  portEXIT_CRITICAL(&g_mux);
  return fresh;
}

// ---- power ------------------------------------------------------------------------------------
bool Esp32Power::begin() {
  g_power_ok = false;
  if (!i2cBusBegin()) return false;
  uint8_t raw[2];
  // The power-on configuration (32 V range, +-320 mV shunt range, continuous) is used as is.
  g_power_ok = readRegs(cfg::INA219_I2C_ADDRESS, kInaRegBus, raw, 2);
  return g_power_ok;
}

bool Esp32Power::read(uint16_t* bus_mv, int32_t* shunt_uv) {
  if (!g_power_ok) return false;
  portENTER_CRITICAL(&g_mux);
  const bool fresh = g.power_valid && millis() - g.power_ms <= kPowerFreshMs;
  if (fresh) {
    *bus_mv = g.bus_mv;
    *shunt_uv = g.shunt_uv;
  }
  portEXIT_CRITICAL(&g_mux);
  return fresh;
}

// ---- ultrasonic: HC-SR04 x4 -------------------------------------------------------------------
namespace {

struct EchoContext {
  uint8_t pin;
  volatile uint32_t rise_us;
  volatile uint32_t fall_us;
  volatile uint8_t state;  // 0 = armed, 1 = echo started, 2 = echo ended
};

EchoContext g_echo[4];
int g_trig[4];
portMUX_TYPE g_echo_mux = portMUX_INITIALIZER_UNLOCKED;

void IRAM_ATTR echoIsr(void* arg) {
  EchoContext* c = static_cast<EchoContext*>(arg);
  const uint32_t now = micros();
  if (digitalRead(c->pin)) {
    if (c->state == 0) {
      c->rise_us = now;
      c->state = 1;
    }
  } else if (c->state == 1) {
    c->fall_us = now;
    c->state = 2;
  }
}

}  // namespace

bool Esp32Ultrasonic::begin() {
  const int trig[4] = {PIN_US_FL_TRIG, PIN_US_FC_TRIG, PIN_US_FR_TRIG, PIN_US_REAR_TRIG};
  const int echo[4] = {PIN_US_FL_ECHO, PIN_US_FC_ECHO, PIN_US_FR_ECHO, PIN_US_REAR_ECHO};
  bool any = false;
  for (int i = 0; i < 4; ++i) {
    present_[i] = pins::allAssigned({trig[i], echo[i]}) && pins::isGpio(echo[i]);
    if (present_[i] && pins::isExpander(trig[i])) {
      // Trigger on the MCP23017: usable only when the I2C bus is (the expander itself is set up
      // by i2cTaskStart()). If the expander does not answer, no ping is ever sent, every reading
      // stays "absent" and driving in that direction stays blocked (fail safe).
      if (!i2cBusBegin()) present_[i] = false;
      g_expander_trig_bit[i] = static_cast<uint8_t>(SCR_PIN_EXPANDER_INDEX(trig[i]));
    }
    if (!present_[i]) continue;  // this sensor is reported as absent
    any = true;
    g_trig[i] = trig[i];
    g_echo[i].pin = static_cast<uint8_t>(echo[i]);
    g_echo[i].state = 0;
    if (pins::isGpio(trig[i])) {
      pinMode(static_cast<uint8_t>(trig[i]), OUTPUT);
      digitalWrite(static_cast<uint8_t>(trig[i]), LOW);
    }
    pinMode(static_cast<uint8_t>(echo[i]), INPUT);  // 5 V echo comes through the level shifter
    attachInterruptArg(static_cast<uint8_t>(echo[i]), echoIsr, &g_echo[i], CHANGE);
  }
  return any;
}

bool Esp32Ultrasonic::present(uint8_t index) const { return index < 4 && present_[index]; }

void Esp32Ultrasonic::trigger(uint8_t index) {
  if (!present(index)) return;
  portENTER_CRITICAL(&g_echo_mux);
  g_echo[index].state = 0;
  portEXIT_CRITICAL(&g_echo_mux);
  if (pins::isExpander(g_trig[index])) {
    // Never on the control task: ask the I2C task and wake it.
    portENTER_CRITICAL(&g_mux);
    g.trigger_mask = static_cast<uint8_t>(g.trigger_mask | (1u << index));
    portEXIT_CRITICAL(&g_mux);
    if (g_i2c_task != nullptr) xTaskNotifyGive(g_i2c_task);
    return;
  }
  digitalWrite(static_cast<uint8_t>(g_trig[index]), HIGH);
  delayMicroseconds(10);
  digitalWrite(static_cast<uint8_t>(g_trig[index]), LOW);
}

hal::EchoState Esp32Ultrasonic::collect(uint8_t index, uint32_t* echo_us) {
  if (!present(index)) return hal::EchoState::NoRise;
  portENTER_CRITICAL(&g_echo_mux);
  const uint8_t state = g_echo[index].state;
  const uint32_t width = g_echo[index].fall_us - g_echo[index].rise_us;
  portEXIT_CRITICAL(&g_echo_mux);
  if (state == 0) return hal::EchoState::NoRise;   // the echo line never went high
  if (state == 1) return hal::EchoState::Timeout;  // still high at the end of the slot
  if (width > cfg::US_ECHO_TIMEOUT_US) return hal::EchoState::Timeout;
  *echo_us = width;
  return hal::EchoState::Done;
}

// ---- system services --------------------------------------------------------------------------
uint8_t Esp32System::resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return proto::kResetPowerOn;
    case ESP_RST_BROWNOUT: return proto::kResetBrownout;
    case ESP_RST_TASK_WDT: return proto::kResetTaskWatchdog;
    case ESP_RST_PANIC: return proto::kResetPanic;
    case ESP_RST_SW: return proto::kResetSoftware;
    case ESP_RST_EXT: return proto::kResetExternal;
    case ESP_RST_INT_WDT:
    case ESP_RST_WDT: return proto::kResetOtherWatchdog;
    case ESP_RST_DEEPSLEEP: return proto::kResetDeepSleep;
    default: return proto::kResetUnknown;
  }
}

uint32_t Esp32System::freeHeap() { return ESP.getFreeHeap(); }

void Esp32System::loopStats(uint16_t* period_max_us, uint16_t* jitter_us) {
  portENTER_CRITICAL(&g_mux);
  const uint32_t mx = g.period_max_us;
  const uint32_t mn = g.period_min_us;
  g.period_max_us = 0;
  g.period_min_us = 0xFFFFFFFFu;
  portEXIT_CRITICAL(&g_mux);
  const uint32_t jit = (mn == 0xFFFFFFFFu || mx < mn) ? 0 : mx - mn;
  *period_max_us = static_cast<uint16_t>(mx > 0xFFFF ? 0xFFFF : mx);
  *jitter_us = static_cast<uint16_t>(jit > 0xFFFF ? 0xFFFF : jit);
}

void Esp32System::requestI2cScan() {
  portENTER_CRITICAL(&g_mux);
  if (g_i2c_task != nullptr) {
    g.scan_requested = true;
    g.scan_done = false;
  } else {
    g.scan_done = true;  // no bus: the (empty) result is ready at once
  }
  portEXIT_CRITICAL(&g_mux);
  if (g_i2c_task != nullptr) xTaskNotifyGive(g_i2c_task);
}

bool Esp32System::i2cScanDone() {
  portENTER_CRITICAL(&g_mux);
  const bool done = g.scan_done;
  portEXIT_CRITICAL(&g_mux);
  return done;
}

uint8_t Esp32System::i2cScanResult(uint8_t* addr, uint8_t cap) {
  portENTER_CRITICAL(&g_mux);
  uint8_t n = g.scan_count < cap ? g.scan_count : cap;
  for (uint8_t i = 0; i < n; ++i) addr[i] = g.scan[i];
  portEXIT_CRITICAL(&g_mux);
  return n;
}

uint8_t Esp32System::takeI2cRecovered() {
  portENTER_CRITICAL(&g_mux);
  const uint8_t a = g.recovered_attempts;
  g.recovered_attempts = 0;
  portEXIT_CRITICAL(&g_mux);
  return a;
}

bool Esp32System::expanderPresent() { return g_expander_ok; }

}  // namespace esp32
}  // namespace scr
