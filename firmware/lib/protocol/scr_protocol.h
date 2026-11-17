// Pi <-> ESP32 serial protocol, version 2 (docs/SERIAL_PROTOCOL.md; v2 = interface spec section A).
// C++ twin of shared/scr_serial (Python). Hardware independent: no Arduino headers.
//
//   on the wire:  COBS( type u8 | seq u8 | body (0..64) | crc16 u16 LE )  0x00
#pragma once

#include <cstddef>
#include <cstdint>

namespace scr {
namespace proto {

constexpr uint32_t kBaudRate = 460800;
constexpr uint32_t kWatchdogMs = 300;
constexpr uint32_t kTelemetryHz = 50;

constexpr size_t kMaxBody = 64;
constexpr size_t kMaxPayload = kMaxBody + 4;       // type, seq, body, crc (2)
constexpr size_t kMaxEncoded = kMaxBody + 4 + 2;   // COBS overhead (1 below 254 bytes) + spare; same as Python
constexpr size_t kMaxFrame = kMaxEncoded + 1;      // + the 0x00 delimiter

constexpr uint16_t kUsNoEcho = 0xFFFF;  // ultrasonic: nothing within range
constexpr uint16_t kUsAbsent = 0xFFFE;  // ultrasonic: sensor missing or faulty
constexpr uint8_t kProtocolVersion = 2;
constexpr uint16_t kParamAll = 0xFFFF;  // PARAM_GET of every parameter

enum class MsgType : uint8_t {
  // Pi -> ESP32
  Drive = 0x01,
  Stop = 0x02,
  Servo = 0x03,
  Mode = 0x04,
  Heartbeat = 0x05,
  CalibrateGyro = 0x06,
  ParamSet = 0x07,     // version 2
  ParamGet = 0x08,
  ParamSave = 0x09,
  DiagRequest = 0x0A,
  Bringup = 0x0B,
  // ESP32 -> Pi
  Telemetry = 0x81,
  Event = 0x82,
  Ack = 0x83,
  ParamReport = 0x84,  // version 2
  DiagReport = 0x85,
  ImuRaw = 0x86,
  UsRaw = 0x87,
};

enum DriveMode : uint8_t {
  kModeSafe = 0,   // motor drivers disabled (power-on state)
  kModeDrive = 1,  // closed-loop track speeds
  kModeTest = 2,   // open loop, first motor test with the tracks off the ground
};

enum Flags : uint16_t {
  kFlagMotorsEnabled = 1u << 0,
  kFlagWatchdogStop = 1u << 1,
  kFlagObstacleStop = 1u << 2,
  kFlagLowBattery = 1u << 3,
  kFlagImuOk = 1u << 4,
  kFlagGyroCalibrating = 1u << 5,
  kFlagSimulated = 1u << 6,  // never set by this firmware: only by the simulated ESP32 (rule 5)
  kFlagEncoderFault = 1u << 7,
  kFlagUltrasonicFault = 1u << 8,
  kFlagPowerSensorOk = 1u << 9,
  kFlagRearObstacleStop = 1u << 10,
  kFlagParamsUnsaved = 1u << 11,        // some parameter differs from what NVS holds
  kFlagBringupActive = 1u << 12,        // a bring-up stream or the servo jog is on (MODE TEST only)
  kFlagVoltageCompFallback = 1u << 13,  // no INA219 reading: duty cap computed for a full 12.6 V pack
};

enum EventCode : uint8_t {
  kEventBoot = 1,
  kEventWatchdogStop = 2,      // arg: ms since the last valid frame
  kEventObstacleStop = 3,      // arg: front distance, mm
  kEventRearObstacleStop = 4,  // arg: rear distance, mm
  kEventLowBattery = 5,        // arg: mV
  kEventImuFault = 6,
  kEventGyroCalDone = 7,       // arg: bias in 0.1 mrad/s
  kEventGyroCalFailed = 8,
  kEventEncoderFault = 9,      // arg: encoder index
  kEventLinkRestored = 10,
  kEventProtocolError = 11,    // arg: bad frames since the last report
  kEventSlip = 12,             // arg: side, 0 left, 1 right
  kEventI2cRecovered = 13,     // arg: bus-clear attempts
  kEventParamChanged = 14,     // arg: parameter id
  kEventServoPulse = 15,       // arg: current pulse in us (answer to BRINGUP SERVO_JOG)
  kEventBatteryCutoff = 16,    // arg: battery mV; motors to SAFE
};

enum AckStatus : uint8_t {
  kAckOk = 0,
  kAckRejectedMode = 1,
  kAckBadArgument = 2,
  kAckBusy = 3,
  kAckUnknownType = 4,
  kAckRejectedSafety = 5,
  kAckOutOfRange = 6,   // PARAM_SET outside the compiled bounds
  kAckUnknownParam = 7,
};

enum BringupKind : uint8_t {
  kBringupOff = 0,
  kBringupImuRaw = 1,    // arg: rate in Hz, 1..200
  kBringupUsRaw = 2,
  kBringupServoJog = 3,  // arg: pulse step in us, |arg| <= 50
};

enum ParamSource : uint8_t {
  kParamSourceDefault = 0,  // compiled default
  kParamSourceNvs = 1,      // loaded from NVS at boot
  kParamSourceSet = 2,      // set since boot, not saved
};

enum BuildEnv : uint8_t {
  kBuildEsp32Dev = 0,
  kBuildEsp32CompileCheck = 1,
  kBuildEsp32CiPins = 2,
  kBuildSimCheck = 3,
  kBuildNativeTest = 4,
  kBuildRobotdSimulated = 5,
};

enum ResetReason : uint8_t {
  kResetUnknown = 0,
  kResetPowerOn = 1,
  kResetBrownout = 2,
  kResetTaskWatchdog = 3,
  kResetPanic = 4,
  kResetSoftware = 5,
  kResetExternal = 6,
  kResetOtherWatchdog = 7,
  kResetDeepSleep = 8,
};

enum DiagFlags : uint8_t {
  kDiagPinsConfirmed = 1u << 0,
  kDiagCiPinsDoNotFlash = 1u << 1,
  kDiagParamsFromNvs = 1u << 2,
  kDiagSimulated = 1u << 3,
};

enum SensorBits : uint8_t {
  kSensorImu = 1u << 0,
  kSensorIna219 = 1u << 1,
  kSensorExpander = 1u << 2,
  kSensorUs0 = 1u << 3,  // + index 0..3
};

struct Drive {
  int16_t left_mm_s;
  int16_t right_mm_s;
};
struct Servo {
  int16_t pan_cdeg;
};
struct Mode {
  uint8_t mode;
};
struct Telemetry {
  uint32_t t_ms;
  int32_t enc[4];         // front-left, rear-left, front-right, rear-right
  int32_t gyro_z_urad_s;  // counter-clockwise positive, bias removed
  int16_t accel_mg[3];    // x forward, y left, z up
  uint16_t us_mm[4];      // front-left, front-centre, front-right, rear
  uint16_t battery_mv;
  int16_t battery_ma;     // positive = discharging
  uint16_t flags;
  uint32_t param_hash;    // version 2: FNV-1a of the current parameters
};
struct Event {
  uint32_t t_ms;
  uint8_t code;
  int16_t arg;
};
struct Ack {
  uint8_t acked_type;
  uint8_t acked_seq;
  uint8_t status;
};

// ---- version 2 ----
struct ParamSet {
  uint16_t id;
  float value;
};
struct ParamGet {
  uint16_t id;  // kParamAll = every parameter
};
struct Bringup {
  uint8_t kind;
  int16_t arg;
};
struct ParamReport {
  uint16_t id;
  float value;
  float min;
  float max;
  uint8_t source;
};
struct DiagReport {
  uint8_t fw_major;
  uint8_t fw_minor;
  uint8_t fw_patch;
  uint32_t git_hash;
  uint8_t build_env;
  uint8_t driver_type;  // 0 L298N, 1 VNH
  uint8_t diag_flags;
  uint32_t param_hash;
  uint8_t reset_reason;
  uint32_t uptime_ms;
  uint16_t loop_period_max_us;
  uint16_t loop_jitter_us;
  uint32_t free_heap;
  uint8_t i2c_count;
  uint8_t i2c_addr[8];
  uint8_t sensors;
};
struct ImuRaw {
  uint32_t t_ms;
  int32_t gyro_urad_s[3];  // forward, left, up; bias NOT removed
  int16_t accel_mg[3];
};
struct UsRaw {
  uint32_t t_ms;
  uint16_t dist_mm[4];
  uint8_t misses[4];
};

constexpr size_t kTelemetryBodySize = 48;
constexpr size_t kEventBodySize = 7;
constexpr size_t kAckBodySize = 3;
constexpr size_t kParamSetBodySize = 6;
constexpr size_t kParamGetBodySize = 2;
constexpr size_t kBringupBodySize = 3;
constexpr size_t kParamReportBodySize = 15;
constexpr size_t kDiagReportBodySize = 37;
constexpr size_t kImuRawBodySize = 22;
constexpr size_t kUsRawBodySize = 16;

// One decoded message. Only the member that matches `type` is meaningful.
struct Message {
  MsgType type;
  Drive drive;
  Servo servo;
  Mode mode;
  Telemetry telemetry;
  Event event;
  Ack ack;
  ParamSet param_set;
  ParamGet param_get;
  Bringup bringup;
  ParamReport param_report;
  DiagReport diag_report;
  ImuRaw imu_raw;
  UsRaw us_raw;
};

Message makeDrive(int16_t left_mm_s, int16_t right_mm_s);
Message makeStop();
Message makeServo(int16_t pan_cdeg);
Message makeMode(uint8_t mode);
Message makeHeartbeat();
Message makeCalibrateGyro();
Message makeTelemetry(const Telemetry& t);
Message makeEvent(uint32_t t_ms, uint8_t code, int16_t arg);
Message makeAck(uint8_t acked_type, uint8_t acked_seq, uint8_t status);
Message makeParamSet(uint16_t id, float value);
Message makeParamGet(uint16_t id);
Message makeParamSave();
Message makeDiagRequest();
Message makeBringup(uint8_t kind, int16_t arg);
Message makeParamReport(uint16_t id, float value, float min, float max, uint8_t source);
Message makeDiagReport(const DiagReport& d);
Message makeImuRaw(const ImuRaw& r);
Message makeUsRaw(const UsRaw& r);

// FNV-1a 32 (docs: interface spec A4). Start with kFnvOffset, feed bytes, the result is the hash.
constexpr uint32_t kFnvOffset = 0x811C9DC5u;
constexpr uint32_t kFnvPrime = 0x01000193u;
uint32_t fnv1aUpdate(uint32_t h, const uint8_t* data, size_t len);
// One parameter's contribution: id u16 LE then value f32 LE (IEEE-754).
uint32_t fnv1aParam(uint32_t h, uint16_t id, float value);

// CRC-16/CCITT-FALSE: poly 0x1021, start 0xFFFF, no reflection, no final XOR. "123456789" -> 0x29B1.
uint16_t crc16Ccitt(const uint8_t* data, size_t len);

// COBS. Both return the number of bytes written, or 0 on error (output too small / damaged input).
// An empty input encodes to one byte (0x01); an empty encoded input is an error.
size_t cobsEncode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap);
size_t cobsDecode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap);

// True and the body length when `type` is a message of this protocol.
bool bodySizeOf(uint8_t type, size_t* size);

// Writes the body of `msg` (little-endian, no padding). Returns its length.
size_t packBody(const Message& msg, uint8_t* out);

// Complete frame including the final 0x00. Returns its length, 0 on error.
size_t encodeFrame(const Message& msg, uint8_t seq, uint8_t* out, size_t out_cap);

enum class DecodeResult : uint8_t {
  Ok = 0,
  BadCobs,
  TooShort,
  BadCrc,
  UnknownType,
  BadLength,
  TooLong,
};

// One frame WITHOUT its 0x00 delimiter. On UnknownType and BadLength, `raw_type` and `seq`
// are still filled in (the CRC was correct), so the receiver can answer with an ACK.
DecodeResult decodeFrame(const uint8_t* encoded, size_t len, Message* msg, uint8_t* seq,
                         uint8_t* raw_type);

// Byte-at-a-time stream decoder. Damaged frames are counted and dropped; the stream
// resynchronises at the next 0x00.
class FrameDecoder {
 public:
  enum class Feed : uint8_t { None, Frame, Bad };

  // Feed::Frame -> message()/seq() are valid. Feed::Bad -> lastError(), and for UnknownType /
  // BadLength also rawType()/seq().
  Feed feed(uint8_t byte);

  const Message& message() const { return msg_; }
  uint8_t seq() const { return seq_; }
  uint8_t rawType() const { return raw_type_; }
  DecodeResult lastError() const { return last_error_; }
  uint32_t badFrames() const { return bad_frames_; }
  uint32_t lostFrames() const { return lost_frames_; }

 private:
  uint8_t buf_[kMaxEncoded];
  size_t len_ = 0;
  bool overflow_ = false;
  bool have_seq_ = false;
  uint8_t last_seq_ = 0;
  Message msg_{};
  uint8_t seq_ = 0;
  uint8_t raw_type_ = 0;
  DecodeResult last_error_ = DecodeResult::Ok;
  uint32_t bad_frames_ = 0;
  uint32_t lost_frames_ = 0;
};

}  // namespace proto
}  // namespace scr
