// I2C bus, imu (ICM-20948), power (INA219), ultrasonic (HC-SR04 x4). NOT VERIFIED ON HARDWARE.
// Register addresses and scale factors are taken from the parts' datasheets and have not been
// checked against real devices.
#include <Wire.h>

#include "esp32_drivers.h"

namespace scr {
namespace esp32 {

// ---- shared I2C bus -------------------------------------------------------------------------
bool i2cBusBegin() {
  static bool started = false;
  static bool ok = false;
  if (started) return ok;
  started = true;
  if (!pins::allAssigned({PIN_I2C_SDA, PIN_I2C_SCL})) return false;
  ok = Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, cfg::I2C_HZ);
  if (ok) Wire.setTimeOut(5);  // ms: a stuck bus must not stall the 100 Hz loop for long
  return ok;
}

bool i2cWriteReg(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool i2cReadRegs(uint8_t address, uint8_t reg, uint8_t* out, size_t len) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(static_cast<int>(address), static_cast<int>(len)) != static_cast<int>(len)) {
    return false;
  }
  for (size_t i = 0; i < len; ++i) out[i] = static_cast<uint8_t>(Wire.read());
  return true;
}

// ---- imu: ICM-20948 -------------------------------------------------------------------------
namespace {
constexpr uint8_t kRegBankSel = 0x7F;
// bank 0
constexpr uint8_t kRegWhoAmI = 0x00;
constexpr uint8_t kWhoAmIValue = 0xEA;
constexpr uint8_t kRegPwrMgmt1 = 0x06;
constexpr uint8_t kRegPwrMgmt2 = 0x07;
constexpr uint8_t kRegAccelXoutH = 0x2D;  // accel x,y,z then gyro x,y,z: 12 bytes, big-endian
// bank 2
constexpr uint8_t kRegGyroConfig1 = 0x01;
constexpr uint8_t kRegAccelConfig = 0x14;
// Full scale +-500 deg/s (65.5 LSB per deg/s) and +-4 g (8192 LSB per g), low-pass filter on.
constexpr uint8_t kGyroConfigValue = (3u << 3) | (1u << 1) | 1u;
constexpr uint8_t kAccelConfigValue = (3u << 3) | (1u << 1) | 1u;
constexpr float kUradPerGyroLsb = (1.0f / 65.5f) * 17453.2925f;  // deg/s -> micro-rad/s
constexpr float kMgPerAccelLsb = 1000.0f / 8192.0f;

bool selectBank(uint8_t bank) {
  return i2cWriteReg(cfg::IMU_I2C_ADDRESS, kRegBankSel, static_cast<uint8_t>(bank << 4));
}
}  // namespace

bool Esp32Imu::begin() {
  ok_ = false;
  if (!i2cBusBegin()) return false;
  const uint8_t addr = cfg::IMU_I2C_ADDRESS;
  uint8_t who = 0;
  if (!selectBank(0) || !i2cReadRegs(addr, kRegWhoAmI, &who, 1) || who != kWhoAmIValue) return false;
  if (!i2cWriteReg(addr, kRegPwrMgmt1, 0x80)) return false;  // device reset
  delay(100);
  if (!selectBank(0)) return false;
  if (!i2cWriteReg(addr, kRegPwrMgmt1, 0x01)) return false;  // wake, best clock source
  delay(20);
  if (!i2cWriteReg(addr, kRegPwrMgmt2, 0x00)) return false;  // accel + gyro on
  if (!selectBank(2)) return false;
  if (!i2cWriteReg(addr, kRegGyroConfig1, kGyroConfigValue)) return false;
  if (!i2cWriteReg(addr, kRegAccelConfig, kAccelConfigValue)) return false;
  if (!selectBank(0)) return false;
  ok_ = true;
  return true;
}

bool Esp32Imu::read(hal::ImuSample* out) {
  if (!ok_) return false;
  uint8_t raw[12];
  if (!i2cReadRegs(cfg::IMU_I2C_ADDRESS, kRegAccelXoutH, raw, sizeof(raw))) return false;
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
  }
  // Rotation about the robot's "up" axis, counter-clockwise positive.
  out->gyro_z_urad_s = static_cast<int32_t>(static_cast<float>(gyro[axis[2] % 3]) * kUradPerGyroLsb *
                                            static_cast<float>(sign[2]));
  return true;
}

// ---- power: INA219 --------------------------------------------------------------------------
namespace {
constexpr uint8_t kInaRegShunt = 0x01;  // signed, 10 uV per LSB
constexpr uint8_t kInaRegBus = 0x02;    // bits 15..3, 4 mV per LSB
}  // namespace

bool Esp32Power::begin() {
  ok_ = false;
  if (!i2cBusBegin()) return false;
  uint8_t raw[2];
  // The power-on configuration (32 V range, +-320 mV shunt range, continuous) is used as is.
  ok_ = i2cReadRegs(cfg::INA219_I2C_ADDRESS, kInaRegBus, raw, 2);
  return ok_;
}

bool Esp32Power::read(uint16_t* bus_mv, int16_t* current_ma) {
  if (!ok_) return false;
  uint8_t b[2];
  uint8_t s[2];
  if (!i2cReadRegs(cfg::INA219_I2C_ADDRESS, kInaRegBus, b, 2)) return false;
  if (!i2cReadRegs(cfg::INA219_I2C_ADDRESS, kInaRegShunt, s, 2)) return false;
  const uint16_t bus_raw = static_cast<uint16_t>((static_cast<uint16_t>(b[0]) << 8) | b[1]);
  const int16_t shunt_raw = static_cast<int16_t>((static_cast<uint16_t>(s[0]) << 8) | s[1]);
  *bus_mv = static_cast<uint16_t>((bus_raw >> 3) * 4u);
  if (cfg::INA219_SHUNT_OHMS <= 0.0f) return false;
  // shunt voltage (10 uV per LSB) / R -> mA
  float ma = static_cast<float>(shunt_raw) * 0.01f / cfg::INA219_SHUNT_OHMS;
  if (cfg::INA219_INVERT_CURRENT) ma = -ma;
  if (ma > 32767.0f) ma = 32767.0f;
  if (ma < -32768.0f) ma = -32768.0f;
  *current_ma = static_cast<int16_t>(ma);
  return true;
}

// ---- ultrasonic: HC-SR04 x4 -----------------------------------------------------------------
namespace {

struct EchoContext {
  uint8_t pin;
  volatile uint32_t rise_us;
  volatile uint32_t fall_us;
  volatile uint8_t state;  // 0 = armed, 1 = echo started, 2 = echo ended
};

EchoContext g_echo[4];
int g_trig[4];

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
    present_[i] = pins::allAssigned({trig[i], echo[i]});
    if (!present_[i]) continue;  // this sensor is reported as absent
    any = true;
    g_trig[i] = trig[i];
    g_echo[i].pin = static_cast<uint8_t>(echo[i]);
    g_echo[i].state = 0;
    pinMode(static_cast<uint8_t>(trig[i]), OUTPUT);
    digitalWrite(static_cast<uint8_t>(trig[i]), LOW);
    pinMode(static_cast<uint8_t>(echo[i]), INPUT);  // 5 V echo comes through the level shifter
    attachInterruptArg(static_cast<uint8_t>(echo[i]), echoIsr, &g_echo[i], CHANGE);
  }
  return any;
}

bool Esp32Ultrasonic::present(uint8_t index) const { return index < 4 && present_[index]; }

void Esp32Ultrasonic::trigger(uint8_t index) {
  if (!present(index)) return;
  noInterrupts();
  g_echo[index].state = 0;
  interrupts();
  digitalWrite(static_cast<uint8_t>(g_trig[index]), HIGH);
  delayMicroseconds(10);
  digitalWrite(static_cast<uint8_t>(g_trig[index]), LOW);
}

hal::EchoState Esp32Ultrasonic::collect(uint8_t index, uint32_t* echo_us) {
  if (!present(index)) return hal::EchoState::NoRise;
  noInterrupts();
  const uint8_t state = g_echo[index].state;
  const uint32_t width = g_echo[index].fall_us - g_echo[index].rise_us;
  interrupts();
  if (state == 0) return hal::EchoState::NoRise;   // the echo line never went high
  if (state == 1) return hal::EchoState::Timeout;  // still high at the end of the slot
  if (width > cfg::US_ECHO_TIMEOUT_US) return hal::EchoState::Timeout;
  *echo_us = width;
  return hal::EchoState::Done;
}

}  // namespace esp32
}  // namespace scr
