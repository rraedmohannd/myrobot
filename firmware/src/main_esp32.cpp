// ESP32 entry point: real drivers + the application, two FreeRTOS tasks with fixed rates.
//   control    100 Hz  link receive, sensors, safety, ramps, PID, outputs
//   telemetry   50 Hz  one TELEMETRY frame
// NOT VERIFIED ON HARDWARE (written before the parts existed).
//
// Safety is local (CLAUDE.md rule 4): the watchdog and the obstacle stop run inside the control
// task and need nothing from the Pi or the backend.
#include <Arduino.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "config.h"
#include "drivers_esp32/esp32_drivers.h"
#include "pins.h"
#include "scr_app.h"

namespace {

scr::esp32::Esp32Motors g_motors;
scr::esp32::Esp32Encoders g_encoders;
scr::esp32::Esp32Imu g_imu;
scr::esp32::Esp32Ultrasonic g_ultrasonic;
scr::esp32::Esp32Servo g_servo;
scr::esp32::Esp32Power g_power;
scr::esp32::Esp32Link g_link;

scr::FirmwareApp* g_app = nullptr;
SemaphoreHandle_t g_lock = nullptr;  // controlTick and telemetryTick never run at the same time

void controlTask(void*) {
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_app->controlTick(millis());
    xSemaphoreGive(g_lock);
    vTaskDelayUntil(&last, pdMS_TO_TICKS(scr::cfg::CONTROL_PERIOD_MS));
  }
}

void telemetryTask(void*) {
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_app->telemetryTick(millis());
    xSemaphoreGive(g_lock);
    vTaskDelayUntil(&last, pdMS_TO_TICKS(scr::cfg::TELEMETRY_PERIOD_MS));
  }
}

}  // namespace

void setup() {
  g_link.begin();

#if defined(PINS_COMPILE_CHECK_ONLY)
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

  static scr::FirmwareApp app(scr::cfg::makeAppConfig(), hw);
  g_app = &app;
  g_lock = xSemaphoreCreateMutex();
  app.begin(millis());  // power-on state: SAFE, drivers disabled

  // Control has the higher priority: safety before reporting.
  xTaskCreatePinnedToCore(controlTask, "control", 8192, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(telemetryTask, "telemetry", 4096, nullptr, 4, nullptr, 1);
}

void loop() { vTaskDelay(pdMS_TO_TICKS(1000)); }
