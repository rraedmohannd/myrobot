#include "scr_protocol.h"

namespace scr {
namespace proto {

namespace {

void putU16(uint8_t*& p, uint16_t v) {
  *p++ = static_cast<uint8_t>(v & 0xFF);
  *p++ = static_cast<uint8_t>(v >> 8);
}
void putI16(uint8_t*& p, int16_t v) { putU16(p, static_cast<uint16_t>(v)); }
void putU32(uint8_t*& p, uint32_t v) {
  for (int i = 0; i < 4; ++i) *p++ = static_cast<uint8_t>((v >> (8 * i)) & 0xFF);
}
void putI32(uint8_t*& p, int32_t v) { putU32(p, static_cast<uint32_t>(v)); }

uint16_t getU16(const uint8_t*& p) {
  uint16_t v = static_cast<uint16_t>(p[0] | (static_cast<uint16_t>(p[1]) << 8));
  p += 2;
  return v;
}
int16_t getI16(const uint8_t*& p) { return static_cast<int16_t>(getU16(p)); }
uint32_t getU32(const uint8_t*& p) {
  uint32_t v = 0;
  for (int i = 0; i < 4; ++i) v |= static_cast<uint32_t>(p[i]) << (8 * i);
  p += 4;
  return v;
}
int32_t getI32(const uint8_t*& p) { return static_cast<int32_t>(getU32(p)); }

Message blank(MsgType type) {
  Message m{};
  m.type = type;
  return m;
}

}  // namespace

Message makeDrive(int16_t left_mm_s, int16_t right_mm_s) {
  Message m = blank(MsgType::Drive);
  m.drive.left_mm_s = left_mm_s;
  m.drive.right_mm_s = right_mm_s;
  return m;
}
Message makeStop() { return blank(MsgType::Stop); }
Message makeServo(int16_t pan_cdeg) {
  Message m = blank(MsgType::Servo);
  m.servo.pan_cdeg = pan_cdeg;
  return m;
}
Message makeMode(uint8_t mode) {
  Message m = blank(MsgType::Mode);
  m.mode.mode = mode;
  return m;
}
Message makeHeartbeat() { return blank(MsgType::Heartbeat); }
Message makeCalibrateGyro() { return blank(MsgType::CalibrateGyro); }
Message makeTelemetry(const Telemetry& t) {
  Message m = blank(MsgType::Telemetry);
  m.telemetry = t;
  return m;
}
Message makeEvent(uint32_t t_ms, uint8_t code, int16_t arg) {
  Message m = blank(MsgType::Event);
  m.event.t_ms = t_ms;
  m.event.code = code;
  m.event.arg = arg;
  return m;
}
Message makeAck(uint8_t acked_type, uint8_t acked_seq, uint8_t status) {
  Message m = blank(MsgType::Ack);
  m.ack.acked_type = acked_type;
  m.ack.acked_seq = acked_seq;
  m.ack.status = status;
  return m;
}

uint16_t crc16Ccitt(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc = static_cast<uint16_t>(crc ^ (static_cast<uint16_t>(data[i]) << 8));
    for (int bit = 0; bit < 8; ++bit) {
      if (crc & 0x8000) {
        crc = static_cast<uint16_t>((crc << 1) ^ 0x1021);
      } else {
        crc = static_cast<uint16_t>(crc << 1);
      }
    }
  }
  return crc;
}

size_t cobsEncode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap) {
  if (out_cap == 0) return 0;
  size_t code_at = 0;
  size_t w = 1;
  uint8_t code = 1;
  for (size_t i = 0; i < len; ++i) {
    if (in[i] == 0) {
      out[code_at] = code;
      if (w >= out_cap) return 0;
      code_at = w++;
      code = 1;
    } else {
      if (w >= out_cap) return 0;
      out[w++] = in[i];
      ++code;
      if (code == 0xFF) {
        out[code_at] = code;
        if (w >= out_cap) return 0;
        code_at = w++;
        code = 1;
      }
    }
  }
  out[code_at] = code;
  return w;
}

size_t cobsDecode(const uint8_t* in, size_t len, uint8_t* out, size_t out_cap) {
  if (len == 0) return 0;
  size_t w = 0;
  size_t i = 0;
  while (i < len) {
    const uint8_t code = in[i];
    if (code == 0) return 0;  // zero byte inside a COBS frame
    const size_t end = i + code;
    if (end > len) return 0;  // cut short
    for (size_t k = i + 1; k < end; ++k) {
      if (in[k] == 0) return 0;
      if (w >= out_cap) return 0;
      out[w++] = in[k];
    }
    i = end;
    if (code < 0xFF && i < len) {
      if (w >= out_cap) return 0;
      out[w++] = 0;
    }
  }
  return w;
}

bool bodySizeOf(uint8_t type, size_t* size) {
  switch (static_cast<MsgType>(type)) {
    case MsgType::Drive: *size = 4; return true;
    case MsgType::Stop: *size = 0; return true;
    case MsgType::Servo: *size = 2; return true;
    case MsgType::Mode: *size = 1; return true;
    case MsgType::Heartbeat: *size = 0; return true;
    case MsgType::CalibrateGyro: *size = 0; return true;
    case MsgType::Telemetry: *size = kTelemetryBodySize; return true;
    case MsgType::Event: *size = kEventBodySize; return true;
    case MsgType::Ack: *size = kAckBodySize; return true;
  }
  return false;
}

size_t packBody(const Message& msg, uint8_t* out) {
  uint8_t* p = out;
  switch (msg.type) {
    case MsgType::Drive:
      putI16(p, msg.drive.left_mm_s);
      putI16(p, msg.drive.right_mm_s);
      break;
    case MsgType::Servo:
      putI16(p, msg.servo.pan_cdeg);
      break;
    case MsgType::Mode:
      *p++ = msg.mode.mode;
      break;
    case MsgType::Telemetry: {
      const Telemetry& t = msg.telemetry;
      putU32(p, t.t_ms);
      for (int i = 0; i < 4; ++i) putI32(p, t.enc[i]);
      putI32(p, t.gyro_z_urad_s);
      for (int i = 0; i < 3; ++i) putI16(p, t.accel_mg[i]);
      for (int i = 0; i < 4; ++i) putU16(p, t.us_mm[i]);
      putU16(p, t.battery_mv);
      putI16(p, t.battery_ma);
      putU16(p, t.flags);
      break;
    }
    case MsgType::Event:
      putU32(p, msg.event.t_ms);
      *p++ = msg.event.code;
      putI16(p, msg.event.arg);
      break;
    case MsgType::Ack:
      *p++ = msg.ack.acked_type;
      *p++ = msg.ack.acked_seq;
      *p++ = msg.ack.status;
      break;
    case MsgType::Stop:
    case MsgType::Heartbeat:
    case MsgType::CalibrateGyro:
      break;
  }
  return static_cast<size_t>(p - out);
}

size_t encodeFrame(const Message& msg, uint8_t seq, uint8_t* out, size_t out_cap) {
  size_t expected = 0;
  if (!bodySizeOf(static_cast<uint8_t>(msg.type), &expected)) return 0;
  uint8_t payload[kMaxPayload];
  payload[0] = static_cast<uint8_t>(msg.type);
  payload[1] = seq;
  size_t n = 2 + packBody(msg, payload + 2);
  if (n != 2 + expected) return 0;
  const uint16_t crc = crc16Ccitt(payload, n);
  payload[n++] = static_cast<uint8_t>(crc & 0xFF);
  payload[n++] = static_cast<uint8_t>(crc >> 8);
  if (out_cap == 0) return 0;
  const size_t enc = cobsEncode(payload, n, out, out_cap - 1);
  if (enc == 0) return 0;
  out[enc] = 0x00;
  return enc + 1;
}

DecodeResult decodeFrame(const uint8_t* encoded, size_t len, Message* msg, uint8_t* seq,
                         uint8_t* raw_type) {
  if (len > kMaxEncoded) return DecodeResult::TooLong;
  uint8_t payload[kMaxEncoded];
  const size_t n = cobsDecode(encoded, len, payload, sizeof(payload));
  if (n == 0) return DecodeResult::BadCobs;
  if (n < 4) return DecodeResult::TooShort;
  const uint16_t got = static_cast<uint16_t>(payload[n - 2] | (static_cast<uint16_t>(payload[n - 1]) << 8));
  if (got != crc16Ccitt(payload, n - 2)) return DecodeResult::BadCrc;
  if (raw_type) *raw_type = payload[0];
  if (seq) *seq = payload[1];
  size_t expected = 0;
  if (!bodySizeOf(payload[0], &expected)) return DecodeResult::UnknownType;
  if (n - 4 != expected) return DecodeResult::BadLength;

  Message m{};
  m.type = static_cast<MsgType>(payload[0]);
  const uint8_t* p = payload + 2;
  switch (m.type) {
    case MsgType::Drive:
      m.drive.left_mm_s = getI16(p);
      m.drive.right_mm_s = getI16(p);
      break;
    case MsgType::Servo:
      m.servo.pan_cdeg = getI16(p);
      break;
    case MsgType::Mode:
      m.mode.mode = *p++;
      break;
    case MsgType::Telemetry: {
      Telemetry& t = m.telemetry;
      t.t_ms = getU32(p);
      for (int i = 0; i < 4; ++i) t.enc[i] = getI32(p);
      t.gyro_z_urad_s = getI32(p);
      for (int i = 0; i < 3; ++i) t.accel_mg[i] = getI16(p);
      for (int i = 0; i < 4; ++i) t.us_mm[i] = getU16(p);
      t.battery_mv = getU16(p);
      t.battery_ma = getI16(p);
      t.flags = getU16(p);
      break;
    }
    case MsgType::Event:
      m.event.t_ms = getU32(p);
      m.event.code = *p++;
      m.event.arg = getI16(p);
      break;
    case MsgType::Ack:
      m.ack.acked_type = *p++;
      m.ack.acked_seq = *p++;
      m.ack.status = *p++;
      break;
    case MsgType::Stop:
    case MsgType::Heartbeat:
    case MsgType::CalibrateGyro:
      break;
  }
  if (msg) *msg = m;
  return DecodeResult::Ok;
}

FrameDecoder::Feed FrameDecoder::feed(uint8_t byte) {
  if (byte != 0) {
    if (len_ >= kMaxEncoded) {
      overflow_ = true;  // garbage without a delimiter: drop until the next 0x00
    } else {
      buf_[len_++] = byte;
    }
    return Feed::None;
  }
  const size_t len = len_;
  const bool overflow = overflow_;
  len_ = 0;
  overflow_ = false;
  if (overflow) {
    ++bad_frames_;
    last_error_ = DecodeResult::TooLong;
    return Feed::Bad;
  }
  if (len == 0) return Feed::None;  // two delimiters in a row
  uint8_t seq = 0;
  uint8_t raw_type = 0;
  const DecodeResult r = decodeFrame(buf_, len, &msg_, &seq, &raw_type);
  if (r != DecodeResult::Ok) {
    ++bad_frames_;
    last_error_ = r;
    seq_ = seq;
    raw_type_ = raw_type;
    return Feed::Bad;
  }
  if (have_seq_) lost_frames_ += static_cast<uint8_t>(seq - last_seq_ - 1);
  have_seq_ = true;
  last_seq_ = seq;
  seq_ = seq;
  raw_type_ = static_cast<uint8_t>(msg_.type);
  last_error_ = DecodeResult::Ok;
  return Feed::Frame;
}

}  // namespace proto
}  // namespace scr
