// ESP32 entry point: real drivers + the application, FreeRTOS tasks with fixed rates.
//   control    100 Hz  link receive, sensors (copies), safety, ramps, PID, outputs   [task WDT]
//   telemetry   50 Hz  one TELEMETRY frame                                          [task WDT]
//   i2c        100 Hz  every I2C transfer (src/drivers_esp32/esp32_sensors.cpp)
// NOT VERIFIED ON HARDWARE (written before the parts existed; never run on an ESP32).
//
// Safety is local (CLAUDE.md rule 4): the watchdog and the obstacle stop run inside the control
// task and need nothing from the Pi or the backend. Every boot, whatever the reset reason
// (power-on, brownout, task watchdog, panic), starts in MODE SAFE with the drivers disabled.
#include <Arduino.h>
#include <esp_task_wdt.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "config.h"
#include "drivers_esp32/esp32_drivers.h"
#include "pins.h"
#include "scr_app.h"

#if !defined(SCR_BUILD_ENV)
#define SCR_BUILD_ENV 0  // esp32dev
#endif

namespace {

scr::esp32::Esp32Motors g_motors;
scr::esp32::Esp32Encoders g_encoders;
scr::esp32::Esp32Imu g_imu;
scr::esp32::Esp32Ultrasonic g_ultrasonic;
scr::esp32::Esp32Servo g_servo;
scr::esp32::Esp32Power g_power;
scr::esp32::Esp32Link g_link;
scr::esp32::Esp32Storage g_storage;
scr::esp32::Esp32System g_system;

scr::FirmwareApp* g_app = nullptr;
SemaphoreHandle_t g_lock = nullptr;  // controlTick and telemetryTick never run at the same time

// Task watchdog (S2.4). A task that stops checking in resets the chip (panic), and the next boot
// is SAFE. The control task waits for the lock without a time limit: if the telemetry task hangs
// while holding it, the control task stops checking in too and the watchdog resets the chip.
void taskWatchdogBegin() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t c = {};
  c.timeout_ms = scr::cfg::TASK_WDT_TIMEOUT_MS;
  c.idle_core_mask = 0;  // the idle tasks are not watched; only the subscribed tasks
  c.trigger_panic = true;
  if (esp_task_wdt_reconfigure(&c) != ESP_OK) esp_task_wdt_init(&c);
#else
  // ESP-IDF 4.4 takes whole seconds: the timeout is rounded UP (500 ms -> 1 s).
  const uint32_t s = (scr::cfg::TASK_WDT_TIMEOUT_MS + 999u) / 1000u;
  esp_task_wdt_init(s, true);  // re-initialising updates timeout and panic (IDF 4.4)
#endif
}

void controlTask(void*) {
  esp_task_wdt_add(nullptr);
  TickType_t last = xTaskGetTickCount();
  uint32_t prev_us = micros();
  for (;;) {
    const uint32_t now_us = micros();
    scr::esp32::recordControlPeriodUs(now_us - prev_us);
    prev_us = now_us;
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_app->controlTick(millis());
    xSemaphoreGive(g_lock);
    esp_task_wdt_reset();
    vTaskDelayUntil(&last, pdMS_TO_TICKS(scr::cfg::CONTROL_PERIOD_MS));
  }
}

void telemetryTask(void*) {
  esp_task_wdt_add(nullptr);
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_app->telemetryTick(millis());
    xSemaphoreGive(g_lock);
    esp_task_wdt_reset();
    vTaskDelayUntil(&last, pdMS_TO_TICKS(scr::cfg::TELEMETRY_PERIOD_MS));
  }
}

}  // namespace

void setup() {
  // 1. FIRST: motor outputs LOW (S2.4), before the serial port or anything else. No-op in the
  //    compile-check and CI builds (they never drive a pin).
  scr::esp32::motorPinsLowAtBoot();

  g_link.begin();

#if defined(SCR_CI_PINS_DO_NOT_FLASH)
  // FAKE pins (include/pins_ci.h), compile only. Every driver refuses to start, the application
  // refuses every mode but SAFE: this firmware never drives a pin. Printed once.
  Serial.println();
  Serial.println("CI build — not flashable");
  Serial.flush();
#elif defined(PINS_COMPILE_CHECK_ONLY)
  // This build exists only to prove that the sources compile. Every pin is unassigned, so every
  // driver refuses to start: MODE DRIVE / TEST are answered with REJECTED_SAFETY, SERVO too.
  // The text below is the only non-protocol output of any build (a Pi would count it as one
  // damaged frame).
  Serial.println();
  Serial.println("SCR FIRMWARE - PINS_COMPILE_CHECK_ONLY BUILD: all pins unassigned, "
                 "no motor, servo or sensor driver will be enabled. Do not use on the robot.");
  Serial.flush();
#endif

  scr::hal::Hardware hw;
  hw.motors = &g_motors;
  hw.encoders = &g_encoders;
  hw.imu = &g_imu;
  hw.ultrasonic = &g_ultrasonic;
  hw.servo = &g_servo;
  hw.power = &g_power;
  hw.link = &g_link;
  hw.storage = &g_storage;
  hw.system = &g_system;

  scr::AppConfig config = scr::cfg::makeAppConfig();
  config.build_env = SCR_BUILD_ENV;
  config.static_diag_flags = 0;
#if defined(PINS_CONFIRMED)
  config.static_diag_flags |= scr::proto::kDiagPinsConfirmed;
#endif
#if defined(SCR_CI_PINS_DO_NOT_FLASH)
  config.static_diag_flags |= scr::proto::kDiagCiPinsDoNotFlash;
  config.permanent_safe = true;
#endif

  static scr::FirmwareApp app(config, hw);
  g_app = &app;
  g_lock = xSemaphoreCreateMutex();
  app.begin(millis());  // power-on state: SAFE, drivers disabled; EVENT BOOT; boot DIAG pending
  scr::esp32::i2cTaskStart();  // after the drivers' begin(): from now on only this task uses I2C

  taskWatchdogBegin();
  // Control has the higher priority: safety before reporting.
  xTaskCreatePinnedToCore(controlTask, "control", 8192, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(telemetryTask, "telemetry", 4096, nullptr, 4, nullptr, 1);
}

void loop() { vTaskDelay(pdMS_TO_TICKS(1000)); }
