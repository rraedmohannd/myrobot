// sim-check: the complete application loop wired to the STUB drivers, built for the build
// machine. It proves that everything hardware-independent compiles and links together, and runs
// a short SIMULATED smoke scenario. Nothing printed here is a physical test result
// (CLAUDE.md rule 5): there is no robot, the "plant" is scr::stub::StubRobot.
#include <cstdio>
#include <vector>

#include "config.h"
#include "scr_app.h"
#include "scr_stub_drivers.h"

using namespace scr;
using namespace scr::proto;

namespace {

struct Sim {
  stub::StubRobot robot;
  FirmwareApp app;
  uint32_t now = 0;
  uint8_t seq = 0;
  FrameDecoder dec;
  std::vector<Event> events;
  std::vector<Ack> acks;
  Telemetry tel{};
  int tel_count = 0;
  std::vector<DiagReport> diags;
  bool telemetry_task_alive = true;  // fault injection: the telemetry "task" stops
  int control_ticks = 0;

  explicit Sim(const AppConfig& c, uint8_t reset_reason = kResetPowerOn) : app(c, robot.hardware()) {
    robot.system.reset_reason = reset_reason;
    app.begin(now);
  }

  void send(const Message& m) {
    uint8_t buf[kMaxFrame];
    const size_t n = encodeFrame(m, seq++, buf, sizeof(buf));
    robot.link.inject(buf, n);
  }
  // One 10 ms control period; telemetry every second period (100 Hz / 50 Hz).
  void step() {
    now += cfg::CONTROL_PERIOD_MS;
    robot.step(static_cast<float>(cfg::CONTROL_PERIOD_MS) / 1000.0f);
    app.controlTick(now);
    ++control_ticks;
    if (telemetry_task_alive && now % cfg::TELEMETRY_PERIOD_MS == 0) app.telemetryTick(now);
    for (uint8_t b : robot.link.tx) {
      if (dec.feed(b) != FrameDecoder::Feed::Frame) continue;
      const Message& m = dec.message();
      if (m.type == MsgType::Event) {
        events.push_back(m.event);
        std::printf("[SIMULATED] t=%5u ms  EVENT code=%u arg=%d\n", static_cast<unsigned>(m.event.t_ms),
                    static_cast<unsigned>(m.event.code), static_cast<int>(m.event.arg));
      } else if (m.type == MsgType::Ack) {
        acks.push_back(m.ack);
        std::printf("[SIMULATED] t=%5u ms  ACK type=0x%02x seq=%u status=%u\n", static_cast<unsigned>(now),
                    static_cast<unsigned>(m.ack.acked_type), static_cast<unsigned>(m.ack.acked_seq),
                    static_cast<unsigned>(m.ack.status));
      } else if (m.type == MsgType::DiagReport) {
        diags.push_back(m.diag_report);
      } else if (m.type == MsgType::Telemetry) {
        tel = m.telemetry;
        ++tel_count;
      }
    }
    robot.link.tx.clear();
  }
  int ackStatus(MsgType type, uint8_t s) const {
    for (const Ack& a : acks) {
      if (a.acked_type == static_cast<uint8_t>(type) && a.acked_seq == s) return a.status;
    }
    return -1;
  }
  int count(uint8_t code) const {
    int n = 0;
    for (const Event& e : events) n += (e.code == code) ? 1 : 0;
    return n;
  }
};

int failures = 0;
void expect(bool ok, const char* what) {
  std::printf("[SIMULATED] %-4s %s\n", ok ? "ok" : "FAIL", what);
  if (!ok) ++failures;
}

// Scenario 1: include/config.h exactly as shipped (placeholders). The robot must not move.
void runPlaceholderConfig() {
  std::printf("[SIMULATED] --- scenario 1: shipped placeholder config.h, 2 simulated seconds ---\n");
  Sim sim(cfg::makeAppConfig());
  for (int i = 0; i < 200; ++i) {
    if (i == 30) sim.send(makeMode(kModeDrive));
    if (i == 40) sim.send(makeMode(kModeTest));
    if (i > 40 && i % 5 == 0) sim.send(makeDrive(300, 300));
    sim.step();
    if (sim.robot.motors.left != 0.0f || sim.robot.motors.right != 0.0f) {
      expect(false, "placeholder config produced a non-zero duty");
      return;
    }
  }
  expect(true, "placeholder config: duty stayed zero (uncalibrated robot does not move)");
  expect(sim.acks.size() >= 1 && sim.acks[0].status == kAckRejectedMode,
         "MODE DRIVE refused while metres_per_tick is not calibrated");
  expect(sim.tel_count == 100, "telemetry at 50 Hz");
}

// Scenario 2: invented SIMULATION-ONLY tuning, to exercise the closed loop and the safety rules.
void runDrivingScenario() {
  std::printf("[SIMULATED] --- scenario 2: simulation-only tuning, 5 simulated seconds ---\n");
  AppConfig c = cfg::makeAppConfig();
  c.metres_per_tick = 0.0001f;  // SIMULATION ONLY, matches StubRobot's arbitrary plant
  c.pid.kp = 1.0f;
  c.pid.ki = 5.0f;
  c.pid.kff = 2.5f;
  c.ramp_mm_s2 = 500.0f;
  c.max_speed_mm_s = 300;
  c.motor_max_duty = 1.0f;      // SIMULATION ONLY (shipped default 0 = nothing moves)
  Sim sim(c);
  bool moved = false;
  bool stopped_by_obstacle = false;
  bool stopped_by_watchdog = false;
  for (int i = 0; i < 500; ++i) {
    const uint32_t t = sim.now;
    if (t == 300) sim.send(makeMode(kModeDrive));
    if (t >= 400 && t < 4000 && t % 50 == 0) sim.send(makeDrive(200, 200));  // Pi: forward, 20 Hz
    if (t == 2000) sim.robot.ultrasonic.setDistanceMm(1, 180.0f);            // obstacle in front
    if (t == 3000) sim.robot.ultrasonic.setNoEcho(1);                        // obstacle removed
    // from t = 4000 the simulated Pi is silent -> watchdog
    sim.step();
    if (t > 1000 && t < 2000 && sim.robot.motors.left > 0.3f) moved = true;
    if (t > 2300 && t < 3000 && sim.robot.motors.left == 0.0f) stopped_by_obstacle = true;
    if (t > 4400 && sim.robot.motors.left == 0.0f) stopped_by_watchdog = true;
  }
  expect(moved, "closed loop drives forward");
  expect(stopped_by_obstacle && sim.count(kEventObstacleStop) == 1,
         "local obstacle stop while the Pi keeps asking for forward");
  expect(stopped_by_watchdog && sim.count(kEventWatchdogStop) == 2,
         "watchdog stop after 300 ms of silence (second one: the first is the power-on silence)");
  expect(sim.tel_count == 250, "telemetry at 50 Hz");
  expect((sim.tel.flags & kFlagSimulated) == 0,
         "firmware does not set the SIMULATED flag itself (only the simulated ESP32 of robotd does)");
}

// SIMULATION-ONLY tuning shared by the fault-injection scenarios.
AppConfig simTuning() {
  AppConfig c = cfg::makeAppConfig();
  c.build_env = kBuildSimCheck;
  c.metres_per_tick = 0.0001f;
  c.pid.kp = 1.0f;
  c.pid.ki = 5.0f;
  c.pid.kff = 2.5f;
  c.ramp_mm_s2 = 500.0f;
  c.max_speed_mm_s = 300;
  c.motor_max_duty = 1.0f;
  return c;
}

// Fault injection 1: a stuck I2C bus. On the ESP32 the I2C task owns the bus and the control
// task only copies its results, so here the IMU simply has no fresh sample, the INA219 neither,
// and the system stub reports one bus recovery. The control loop keeps its period, the local
// obstacle stop keeps working.
void runStuckI2c() {
  std::printf("[SIMULATED] --- fault injection 1: stuck I2C bus (IMU + INA219 silent, then bus recovery) ---\n");
  Sim sim(simTuning());
  bool stopped_by_obstacle = false;
  int tel_before = 0;
  for (int i = 0; i < 400; ++i) {
    const uint32_t t = sim.now;
    if (t == 300) sim.send(makeMode(kModeDrive));
    if (t >= 400 && t % 50 == 0) sim.send(makeDrive(200, 200));
    if (t == 1000) {
      sim.robot.imu.read_result = false;    // I2C reads time out: no fresh sample
      sim.robot.power.read_result = false;
      tel_before = sim.tel_count;
    }
    if (t == 2000) sim.robot.ultrasonic.setDistanceMm(1, 180.0f);  // obstacle during the fault
    if (t == 2500) {
      sim.robot.system.recovered_attempts = 1;  // the I2C task cleared the bus
      sim.robot.imu.read_result = true;
      sim.robot.power.read_result = true;
    }
    sim.step();
    if (t > 2300 && t < 2500 && sim.robot.motors.left == 0.0f) stopped_by_obstacle = true;
  }
  expect(sim.control_ticks == 400, "control loop ran every 10 ms during the I2C fault (400/400 ticks)");
  expect(sim.tel_count - tel_before == 150, "telemetry kept 50 Hz during the I2C fault");
  expect(sim.count(kEventImuFault) == 1, "IMU_FAULT reported once");
  expect(sim.count(kEventI2cRecovered) == 1, "I2C_RECOVERED reported after the bus clear");
  expect(stopped_by_obstacle, "local obstacle stop still works while I2C is stuck");
  expect((sim.tel.flags & kFlagImuOk) != 0, "IMU_OK again after the recovery");
}

// Fault injection 2: the telemetry task hangs. The software model of the task watchdog
// (SoftTaskWatchdog, same rule as esp_task_wdt on the ESP32) fires; the chip resets; the new
// boot is SAFE and DIAG reports the task-watchdog reset.
void runHungTelemetryTask() {
  std::printf("[SIMULATED] --- fault injection 2: telemetry task hangs -> task watchdog -> reset ---\n");
  bool was_driving = false;
  int fired_at = -1;
  {
    Sim sim(simTuning());
    SoftTaskWatchdog wdt(cfg::TASK_WDT_TIMEOUT_MS);
    const int control = wdt.add(sim.now);
    const int telemetry = wdt.add(sim.now);
    for (int i = 0; i < 300 && fired_at < 0; ++i) {
      const uint32_t t = sim.now;
      if (t == 300) sim.send(makeMode(kModeDrive));
      if (t >= 400 && t % 50 == 0) sim.send(makeDrive(200, 200));
      if (t == 1000) sim.telemetry_task_alive = false;  // hangs
      sim.step();
      wdt.feed(control, sim.now);
      if (sim.telemetry_task_alive) wdt.feed(telemetry, sim.now);
      if (t > 800 && t < 1000 && sim.robot.motors.enabled) was_driving = true;
      if (wdt.expired(sim.now) == telemetry) fired_at = static_cast<int>(sim.now);
    }
  }
  expect(was_driving, "the robot was driving before the hang");
  expect(fired_at >= 1000 + static_cast<int>(cfg::TASK_WDT_TIMEOUT_MS) && fired_at <= 1000 + static_cast<int>(cfg::TASK_WDT_TIMEOUT_MS) + 20,
         "task watchdog fired TASK_WDT_TIMEOUT_MS after the telemetry task stopped");
  // "reset": a new boot with the reset reason the ESP32 would report
  Sim after(simTuning(), kResetTaskWatchdog);
  after.step();
  expect(after.app.safety().mode() == kModeSafe && !after.robot.motors.enabled,
         "after the watchdog reset the firmware boots in SAFE, drivers disabled");
  expect(!after.diags.empty() && after.diags[0].reset_reason == kResetTaskWatchdog,
         "boot DIAG_REPORT says reset_reason = task watchdog");
}

// Fault injection 3: a parameter outside its compiled bounds.
void runParamOutOfRange() {
  std::printf("[SIMULATED] --- fault injection 3: PARAM_SET out of range ---\n");
  Sim sim(simTuning());
  sim.step();
  const uint32_t hash_before = sim.app.params().hash();
  const uint8_t s = sim.seq;
  sim.send(makeParamSet(params::OBSTACLE_THRESHOLD_MM, 100.0f));  // below the hard minimum 150
  sim.step();
  const uint8_t s2 = sim.seq;
  sim.send(makeParamSet(params::MOTOR_MAX_DUTY, 1.5f));
  sim.step();
  expect(sim.ackStatus(MsgType::ParamSet, s) == kAckOutOfRange && sim.ackStatus(MsgType::ParamSet, s2) == kAckOutOfRange,
         "PARAM_SET outside [min, max] answered OUT_OF_RANGE");
  expect(sim.app.params().get(params::OBSTACLE_THRESHOLD_MM) == 250.0f && sim.app.config().safety.obstacle_mm == 250,
         "value unchanged (250 mm) and still used by the obstacle stop");
  expect(sim.app.params().hash() == hash_before, "parameter hash unchanged");
}

}  // namespace

int main() {
  std::printf("[SIMULATED] sim-check: firmware application with STUB drivers. No hardware. Not a physical result.\n");
  runPlaceholderConfig();
  runDrivingScenario();
  runStuckI2c();
  runHungTelemetryTask();
  runParamOutOfRange();
  std::printf("[SIMULATED] sim-check finished: %d failure(s)\n", failures);
  return failures == 0 ? 0 : 1;
}
