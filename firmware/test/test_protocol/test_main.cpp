// Protocol tests + the cross-test with the Python implementation (docs/SERIAL_PROTOCOL.md 6):
//   (a) encodes the canonical vectors and WRITES shared/serial_fixtures/frames_from_cpp.txt
//   (b) READS shared/serial_fixtures/frames_from_python.txt, decodes every frame, compares every
//       field with the canonical values and the bytes with this implementation's encoding.
#include <unity.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "scr_protocol.h"

using namespace scr::proto;

#define SCR_STR2(x) #x
#define SCR_STR(x) SCR_STR2(x)

void setUp() {}
void tearDown() {}

// ---- canonical vectors (docs/SERIAL_PROTOCOL.md section 6) -----------------------------------
struct Vector {
  const char* name;
  uint8_t seq;
  Message msg;
};

static std::vector<Vector> canonical() {
  Telemetry t{};
  t.t_ms = 123456;
  t.enc[0] = 1000;
  t.enc[1] = -2000;
  t.enc[2] = 300000;
  t.enc[3] = -4;
  t.gyro_z_urad_s = -12345;
  t.accel_mg[0] = 12;
  t.accel_mg[1] = -34;
  t.accel_mg[2] = 1001;
  t.us_mm[0] = 250;
  t.us_mm[1] = 1200;
  t.us_mm[2] = 0xFFFF;
  t.us_mm[3] = 0xFFFE;
  t.battery_mv = 11850;
  t.battery_ma = 1520;
  t.flags = 0x0051;
  Telemetry zeros{};
  return {
      {"drive", 1, makeDrive(250, -125)},
      {"stop", 2, makeStop()},
      {"servo", 3, makeServo(-1234)},
      {"mode", 4, makeMode(kModeDrive)},
      {"heartbeat", 5, makeHeartbeat()},
      {"calibrate_gyro", 6, makeCalibrateGyro()},
      {"telemetry", 200, makeTelemetry(t)},
      {"telemetry_zeros", 0, makeTelemetry(zeros)},
      {"event", 201, makeEvent(5000, kEventObstacleStop, 180)},
      {"ack", 202, makeAck(0x04, 4, kAckOk)},
  };
}

static std::string toHex(const uint8_t* d, size_t n) {
  static const char* digits = "0123456789abcdef";
  std::string s;
  for (size_t i = 0; i < n; ++i) {
    s.push_back(digits[d[i] >> 4]);
    s.push_back(digits[d[i] & 15]);
  }
  return s;
}

static std::vector<uint8_t> fromHex(const std::string& s) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < s.size(); i += 2) {
    out.push_back(static_cast<uint8_t>(std::strtoul(s.substr(i, 2).c_str(), nullptr, 16)));
  }
  return out;
}

static std::vector<uint8_t> encode(const Message& m, uint8_t seq) {
  uint8_t buf[kMaxFrame];
  const size_t n = encodeFrame(m, seq, buf, sizeof(buf));
  return std::vector<uint8_t>(buf, buf + n);
}

static void assertSameMessage(const Message& a, const Message& b, const char* name) {
  TEST_ASSERT_EQUAL_UINT8_MESSAGE(static_cast<uint8_t>(a.type), static_cast<uint8_t>(b.type), name);
  switch (a.type) {
    case MsgType::Drive:
      TEST_ASSERT_EQUAL_INT16_MESSAGE(a.drive.left_mm_s, b.drive.left_mm_s, name);
      TEST_ASSERT_EQUAL_INT16_MESSAGE(a.drive.right_mm_s, b.drive.right_mm_s, name);
      break;
    case MsgType::Servo:
      TEST_ASSERT_EQUAL_INT16_MESSAGE(a.servo.pan_cdeg, b.servo.pan_cdeg, name);
      break;
    case MsgType::Mode:
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(a.mode.mode, b.mode.mode, name);
      break;
    case MsgType::Telemetry:
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(a.telemetry.t_ms, b.telemetry.t_ms, name);
      TEST_ASSERT_EQUAL_INT32_ARRAY_MESSAGE(a.telemetry.enc, b.telemetry.enc, 4, name);
      TEST_ASSERT_EQUAL_INT32_MESSAGE(a.telemetry.gyro_z_urad_s, b.telemetry.gyro_z_urad_s, name);
      TEST_ASSERT_EQUAL_INT16_ARRAY_MESSAGE(a.telemetry.accel_mg, b.telemetry.accel_mg, 3, name);
      TEST_ASSERT_EQUAL_UINT16_ARRAY_MESSAGE(a.telemetry.us_mm, b.telemetry.us_mm, 4, name);
      TEST_ASSERT_EQUAL_UINT16_MESSAGE(a.telemetry.battery_mv, b.telemetry.battery_mv, name);
      TEST_ASSERT_EQUAL_INT16_MESSAGE(a.telemetry.battery_ma, b.telemetry.battery_ma, name);
      TEST_ASSERT_EQUAL_UINT16_MESSAGE(a.telemetry.flags, b.telemetry.flags, name);
      break;
    case MsgType::Event:
      TEST_ASSERT_EQUAL_UINT32_MESSAGE(a.event.t_ms, b.event.t_ms, name);
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(a.event.code, b.event.code, name);
      TEST_ASSERT_EQUAL_INT16_MESSAGE(a.event.arg, b.event.arg, name);
      break;
    case MsgType::Ack:
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(a.ack.acked_type, b.ack.acked_type, name);
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(a.ack.acked_seq, b.ack.acked_seq, name);
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(a.ack.status, b.ack.status, name);
      break;
    default:
      break;
  }
}

// ---- fixture directory -----------------------------------------------------------------------
static bool dirHasPythonFixture(const std::string& dir) {
  std::ifstream f(dir + "/frames_from_python.txt");
  return f.good();
}

static std::string fixtureDir() {
  std::vector<std::string> candidates;
  if (const char* env = std::getenv("SCR_FIXTURE_DIR")) candidates.push_back(env);
#ifdef SCR_FIXTURE_DIR
  candidates.push_back(SCR_STR(SCR_FIXTURE_DIR));
#endif
  candidates.push_back("../shared/serial_fixtures");     // run from firmware/
  candidates.push_back("shared/serial_fixtures");        // run from the repo root
  candidates.push_back("../../shared/serial_fixtures");
  for (const std::string& c : candidates) {
    if (dirHasPythonFixture(c)) return c;
  }
  return "";
}

// ---- tests -----------------------------------------------------------------------------------
static void test_crc_check_value() {
  const char* s = "123456789";
  TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16Ccitt(reinterpret_cast<const uint8_t*>(s), 9));
  TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16Ccitt(nullptr, 0));
}

static void test_cobs_known_vectors() {
  uint8_t out[16];
  const uint8_t a[] = {0x00};
  TEST_ASSERT_EQUAL(2, cobsEncode(a, 1, out, sizeof(out)));
  TEST_ASSERT_EQUAL_HEX8(0x01, out[0]);
  TEST_ASSERT_EQUAL_HEX8(0x01, out[1]);
  const uint8_t b[] = {0x11, 0x22, 0x00, 0x33};
  const uint8_t b_enc[] = {0x03, 0x11, 0x22, 0x02, 0x33};
  TEST_ASSERT_EQUAL(5, cobsEncode(b, 4, out, sizeof(out)));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(b_enc, out, 5);
  const uint8_t c[] = {0x11, 0x00, 0x00, 0x00};
  const uint8_t c_enc[] = {0x02, 0x11, 0x01, 0x01, 0x01};
  TEST_ASSERT_EQUAL(5, cobsEncode(c, 4, out, sizeof(out)));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(c_enc, out, 5);
  TEST_ASSERT_EQUAL(1, cobsEncode(nullptr, 0, out, sizeof(out)));
  TEST_ASSERT_EQUAL_HEX8(0x01, out[0]);
}

static void test_cobs_round_trip_all_lengths() {
  // Pseudo-random data with many zeros, every length up to the largest payload.
  uint32_t x = 12345;
  for (size_t len = 0; len <= kMaxPayload; ++len) {
    uint8_t in[kMaxPayload];
    for (size_t i = 0; i < len; ++i) {
      x = x * 1664525u + 1013904223u;
      in[i] = ((x >> 24) % 3 == 0) ? 0 : static_cast<uint8_t>(x >> 16);
    }
    uint8_t enc[kMaxPayload + 4];
    const size_t n = cobsEncode(in, len, enc, sizeof(enc));
    TEST_ASSERT_TRUE(n >= 1);
    for (size_t i = 0; i < n; ++i) TEST_ASSERT_NOT_EQUAL(0, enc[i]);
    uint8_t dec[kMaxPayload + 4];
    const size_t m = cobsDecode(enc, n, dec, sizeof(dec));
    TEST_ASSERT_EQUAL(len, m);
    if (len) TEST_ASSERT_EQUAL_HEX8_ARRAY(in, dec, len);
  }
}

static void test_cobs_decode_rejects_damage() {
  uint8_t out[16];
  const uint8_t zero_inside[] = {0x03, 0x11, 0x00};
  TEST_ASSERT_EQUAL(0, cobsDecode(zero_inside, 3, out, sizeof(out)));
  const uint8_t cut_short[] = {0x05, 0x11, 0x22};
  TEST_ASSERT_EQUAL(0, cobsDecode(cut_short, 3, out, sizeof(out)));
  TEST_ASSERT_EQUAL(0, cobsDecode(nullptr, 0, out, sizeof(out)));
}

static void test_drive_example_from_the_document() {
  const uint8_t expected[] = {0x04, 0x01, 0x01, 0xfa, 0x05, 0x83, 0xff, 0x3c, 0x46, 0x00};
  const std::vector<uint8_t> f = encode(makeDrive(250, -125), 1);
  TEST_ASSERT_EQUAL(sizeof(expected), f.size());
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, f.data(), sizeof(expected));
}

static void test_body_sizes() {
  size_t n = 0;
  TEST_ASSERT_TRUE(bodySizeOf(0x01, &n)); TEST_ASSERT_EQUAL(4, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x02, &n)); TEST_ASSERT_EQUAL(0, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x03, &n)); TEST_ASSERT_EQUAL(2, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x04, &n)); TEST_ASSERT_EQUAL(1, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x05, &n)); TEST_ASSERT_EQUAL(0, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x06, &n)); TEST_ASSERT_EQUAL(0, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x81, &n)); TEST_ASSERT_EQUAL(44, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x82, &n)); TEST_ASSERT_EQUAL(7, n);
  TEST_ASSERT_TRUE(bodySizeOf(0x83, &n)); TEST_ASSERT_EQUAL(3, n);
  TEST_ASSERT_FALSE(bodySizeOf(0x00, &n));
  TEST_ASSERT_FALSE(bodySizeOf(0x07, &n));
  TEST_ASSERT_FALSE(bodySizeOf(0x84, &n));
}

static void test_every_message_round_trips() {
  for (const Vector& v : canonical()) {
    const std::vector<uint8_t> f = encode(v.msg, v.seq);
    TEST_ASSERT_TRUE_MESSAGE(f.size() >= 6, v.name);
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(0x00, f.back(), v.name);
    for (size_t i = 0; i + 1 < f.size(); ++i) TEST_ASSERT_NOT_EQUAL_MESSAGE(0, f[i], v.name);
    Message out{};
    uint8_t seq = 0, raw = 0;
    TEST_ASSERT_TRUE_MESSAGE(
        decodeFrame(f.data(), f.size() - 1, &out, &seq, &raw) == DecodeResult::Ok, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.seq, seq, v.name);
    assertSameMessage(v.msg, out, v.name);
  }
}

static void test_extreme_values_round_trip() {
  Telemetry t{};
  t.t_ms = 0xFFFFFFFFu;
  t.enc[0] = INT32_MAX;
  t.enc[1] = INT32_MIN;
  t.enc[2] = -1;
  t.enc[3] = 1;
  t.gyro_z_urad_s = INT32_MIN;
  t.accel_mg[0] = INT16_MIN;
  t.accel_mg[1] = INT16_MAX;
  t.accel_mg[2] = -1;
  t.us_mm[0] = 0;
  t.us_mm[1] = 0xFFFF;
  t.us_mm[2] = 0xFFFE;
  t.us_mm[3] = 1;
  t.battery_mv = 0xFFFF;
  t.battery_ma = INT16_MIN;
  t.flags = 0x07FF;
  const Message msgs[] = {makeTelemetry(t), makeDrive(INT16_MIN, INT16_MAX), makeServo(INT16_MIN),
                          makeEvent(0xFFFFFFFFu, 11, INT16_MIN), makeAck(0xFF, 0xFF, 5), makeMode(2)};
  for (const Message& m : msgs) {
    const std::vector<uint8_t> f = encode(m, 255);
    Message out{};
    uint8_t seq = 0;
    TEST_ASSERT_TRUE(decodeFrame(f.data(), f.size() - 1, &out, &seq, nullptr) == DecodeResult::Ok);
    TEST_ASSERT_EQUAL_UINT8(255, seq);
    assertSameMessage(m, out, "extreme");
  }
}

static std::vector<uint8_t> rawFrame(std::vector<uint8_t> payload_without_crc, bool good_crc = true) {
  uint16_t crc = crc16Ccitt(payload_without_crc.data(), payload_without_crc.size());
  if (!good_crc) crc ^= 0x0100;
  payload_without_crc.push_back(static_cast<uint8_t>(crc & 0xFF));
  payload_without_crc.push_back(static_cast<uint8_t>(crc >> 8));
  uint8_t enc[kMaxFrame + 8];
  const size_t n = cobsEncode(payload_without_crc.data(), payload_without_crc.size(), enc, sizeof(enc));
  std::vector<uint8_t> f(enc, enc + n);
  f.push_back(0);
  return f;
}

static void test_bad_crc_is_dropped() {
  std::vector<uint8_t> f = encode(makeDrive(100, 100), 9);
  f[3] ^= 0x40;  // damage one body byte (stays non-zero)
  TEST_ASSERT_TRUE(decodeFrame(f.data(), f.size() - 1, nullptr, nullptr, nullptr) == DecodeResult::BadCrc);
  FrameDecoder d;
  FrameDecoder::Feed last = FrameDecoder::Feed::None;
  for (uint8_t b : f) last = d.feed(b);
  TEST_ASSERT_TRUE(last == FrameDecoder::Feed::Bad);
  TEST_ASSERT_TRUE(d.lastError() == DecodeResult::BadCrc);
  TEST_ASSERT_EQUAL_UINT32(1, d.badFrames());
}

static void test_unknown_type_and_wrong_length() {
  uint8_t seq = 0, raw = 0;
  std::vector<uint8_t> unknown = rawFrame({0x7F, 42});
  TEST_ASSERT_TRUE(decodeFrame(unknown.data(), unknown.size() - 1, nullptr, &seq, &raw) ==
                   DecodeResult::UnknownType);
  TEST_ASSERT_EQUAL_HEX8(0x7F, raw);
  TEST_ASSERT_EQUAL_UINT8(42, seq);
  std::vector<uint8_t> long_drive = rawFrame({0x01, 7, 1, 2, 3, 4, 5});
  TEST_ASSERT_TRUE(decodeFrame(long_drive.data(), long_drive.size() - 1, nullptr, &seq, &raw) ==
                   DecodeResult::BadLength);
  std::vector<uint8_t> stop_with_body = rawFrame({0x02, 7, 1});
  TEST_ASSERT_TRUE(decodeFrame(stop_with_body.data(), stop_with_body.size() - 1, nullptr, &seq, &raw) ==
                   DecodeResult::BadLength);
  const uint8_t tiny[] = {0x02, 0x01};
  TEST_ASSERT_TRUE(decodeFrame(tiny, 2, nullptr, nullptr, nullptr) == DecodeResult::TooShort);
}

static void test_resync_after_garbage() {
  FrameDecoder d;
  int frames = 0;
  // Garbage (no delimiter), then a half frame joined in the middle, then good frames.
  const uint8_t garbage[] = {0x55, 0xAA, 0x13, 0x37, 0x99};
  for (uint8_t b : garbage) TEST_ASSERT_TRUE(d.feed(b) == FrameDecoder::Feed::None);
  std::vector<uint8_t> half = encode(makeDrive(1, 2), 1);
  for (size_t i = 4; i < half.size(); ++i) {
    TEST_ASSERT_FALSE(d.feed(half[i]) == FrameDecoder::Feed::Frame);
  }
  TEST_ASSERT_EQUAL_UINT32(1, d.badFrames());
  for (uint8_t b : encode(makeHeartbeat(), 2)) {
    if (d.feed(b) == FrameDecoder::Feed::Frame) ++frames;
  }
  TEST_ASSERT_EQUAL(1, frames);
  TEST_ASSERT_TRUE(d.message().type == MsgType::Heartbeat);
  TEST_ASSERT_EQUAL_UINT8(2, d.seq());
  // Two delimiters in a row are not an error.
  TEST_ASSERT_TRUE(d.feed(0) == FrameDecoder::Feed::None);
  TEST_ASSERT_EQUAL_UINT32(1, d.badFrames());
}

static void test_overlong_garbage_is_one_bad_frame() {
  FrameDecoder d;
  for (int i = 0; i < 500; ++i) TEST_ASSERT_TRUE(d.feed(0x41) == FrameDecoder::Feed::None);
  TEST_ASSERT_TRUE(d.feed(0) == FrameDecoder::Feed::Bad);
  TEST_ASSERT_TRUE(d.lastError() == DecodeResult::TooLong);
  TEST_ASSERT_EQUAL_UINT32(1, d.badFrames());
  int frames = 0;
  for (uint8_t b : encode(makeStop(), 3)) {
    if (d.feed(b) == FrameDecoder::Feed::Frame) ++frames;
  }
  TEST_ASSERT_EQUAL(1, frames);
}

static void test_sequence_gaps_are_counted_not_rejected() {
  FrameDecoder d;
  const uint8_t seqs[] = {250, 251, 254, 255, 0, 3};  // lost: 252, 253, then 1, 2
  int frames = 0;
  for (uint8_t s : seqs) {
    for (uint8_t b : encode(makeHeartbeat(), s)) {
      if (d.feed(b) == FrameDecoder::Feed::Frame) ++frames;
    }
  }
  TEST_ASSERT_EQUAL(6, frames);
  TEST_ASSERT_EQUAL_UINT32(4, d.lostFrames());
  TEST_ASSERT_EQUAL_UINT32(0, d.badFrames());
}

static void test_stream_of_all_messages_byte_by_byte() {
  FrameDecoder d;
  std::vector<Vector> vs = canonical();
  size_t got = 0;
  for (const Vector& v : vs) {
    for (uint8_t b : encode(v.msg, v.seq)) {
      if (d.feed(b) == FrameDecoder::Feed::Frame) {
        assertSameMessage(v.msg, d.message(), v.name);
        TEST_ASSERT_EQUAL_UINT8(v.seq, d.seq());
        ++got;
      }
    }
  }
  TEST_ASSERT_EQUAL(vs.size(), got);
}

// ---- cross-test with Python ------------------------------------------------------------------
static void test_write_cpp_fixture() {
  const std::string dir = fixtureDir();
  TEST_ASSERT_FALSE_MESSAGE(dir.empty(),
                            "fixture directory not found (set SCR_FIXTURE_DIR or run from firmware/)");
  const std::string path = dir + "/frames_from_cpp.txt";
  std::ofstream out(path, std::ios::binary | std::ios::trunc);  // binary: LF line ends everywhere
  TEST_ASSERT_TRUE_MESSAGE(out.good(), path.c_str());
  for (const Vector& v : canonical()) {
    const std::vector<uint8_t> f = encode(v.msg, v.seq);
    out << v.name << ' ' << static_cast<unsigned>(v.seq) << ' ' << toHex(f.data(), f.size()) << '\n';
  }
  out.close();
  TEST_ASSERT_TRUE_MESSAGE(out.good(), "write failed");
  std::printf("wrote %s\n", path.c_str());
}

static void test_read_python_fixture() {
  const std::string dir = fixtureDir();
  TEST_ASSERT_FALSE_MESSAGE(dir.empty(),
                            "fixture directory not found (set SCR_FIXTURE_DIR or run from firmware/)");
  std::ifstream in(dir + "/frames_from_python.txt");
  TEST_ASSERT_TRUE(in.good());
  std::map<std::string, std::pair<int, std::vector<uint8_t>>> frames;
  std::string line;
  while (std::getline(in, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty() || line[0] == '#') continue;
    std::istringstream ss(line);
    std::string name, hex;
    int seq = -1;
    ss >> name >> seq >> hex;
    TEST_ASSERT_FALSE_MESSAGE(name.empty() || hex.empty() || seq < 0, line.c_str());
    frames[name] = {seq, fromHex(hex)};
  }
  const std::vector<Vector> vs = canonical();
  TEST_ASSERT_EQUAL_MESSAGE(vs.size(), frames.size(), "number of frames in frames_from_python.txt");
  for (const Vector& v : vs) {
    TEST_ASSERT_TRUE_MESSAGE(frames.count(v.name) == 1, v.name);
    const int file_seq = frames[v.name].first;
    const std::vector<uint8_t>& bytes = frames[v.name].second;
    TEST_ASSERT_EQUAL_INT_MESSAGE(v.seq, file_seq, v.name);
    TEST_ASSERT_TRUE_MESSAGE(bytes.size() >= 2 && bytes.back() == 0, v.name);
    // decode and compare every field
    Message out{};
    uint8_t seq = 0;
    TEST_ASSERT_TRUE_MESSAGE(
        decodeFrame(bytes.data(), bytes.size() - 1, &out, &seq, nullptr) == DecodeResult::Ok, v.name);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(v.seq, seq, v.name);
    assertSameMessage(v.msg, out, v.name);
    // compare the bytes with our own encoding
    const std::vector<uint8_t> mine = encode(v.msg, v.seq);
    TEST_ASSERT_EQUAL_MESSAGE(mine.size(), bytes.size(), v.name);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(mine.data(), bytes.data(), mine.size(), v.name);
    // and through the stream decoder
    FrameDecoder d;
    int got = 0;
    for (uint8_t b : bytes) {
      if (d.feed(b) == FrameDecoder::Feed::Frame) ++got;
    }
    TEST_ASSERT_EQUAL_INT_MESSAGE(1, got, v.name);
  }
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_crc_check_value);
  RUN_TEST(test_cobs_known_vectors);
  RUN_TEST(test_cobs_round_trip_all_lengths);
  RUN_TEST(test_cobs_decode_rejects_damage);
  RUN_TEST(test_drive_example_from_the_document);
  RUN_TEST(test_body_sizes);
  RUN_TEST(test_every_message_round_trips);
  RUN_TEST(test_extreme_values_round_trip);
  RUN_TEST(test_bad_crc_is_dropped);
  RUN_TEST(test_unknown_type_and_wrong_length);
  RUN_TEST(test_resync_after_garbage);
  RUN_TEST(test_overlong_garbage_is_one_bad_frame);
  RUN_TEST(test_sequence_gaps_are_counted_not_rejected);
  RUN_TEST(test_stream_of_all_messages_byte_by_byte);
  RUN_TEST(test_write_cpp_fixture);
  RUN_TEST(test_read_python_fixture);
  return UNITY_END();
}
