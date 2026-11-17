// Pi <-> ESP32 serial protocol, version 1 (docs/SERIAL_PROTOCOL.md).
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

enum class MsgType : uint8_t {
  // Pi -> ESP32
  Drive = 0x01,
  Stop = 0x02,
  Servo = 0x03,
  Mode = 0x04,
  Heartbeat = 0x05,
  CalibrateGyro = 0x06,
  // ESP32 -> Pi
  Telemetry = 0x81,
  Event = 0x82,
  Ack = 0x83,
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
};

enum AckStatus : uint8_t {
  kAckOk = 0,
  kAckRejectedMode = 1,
  kAckBadArgument = 2,
  kAckBusy = 3,
  kAckUnknownType = 4,
  kAckRejectedSafety = 5,
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

constexpr size_t kTelemetryBodySize = 44;
constexpr size_t kEventBodySize = 7;
constexpr size_t kAckBodySize = 3;

// One decoded message. Only the member that matches `type` is meaningful.
struct Message {
  MsgType type;
  Drive drive;
  Servo servo;
  Mode mode;
  Telemetry telemetry;
  Event event;
  Ack ack;
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
