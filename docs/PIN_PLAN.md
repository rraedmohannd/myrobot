# PROPOSAL — the firmware does not use this file

**Pin plan on paper for the ESP32 DevKit 38-pin (ESP32-WROOM-32).** Written on 2026-11-17 (sprint 5, S1.1), before any part was bought or wired. Nothing here is a physical result (CLAUDE.md rule 5).

How pins really get into the firmware (CLAUDE.md rule 8):

1. Mohannd checks this proposal with his supervisor, buys the parts, wires the robot.
2. He writes the pins **as wired** into the pin-map table of [`docs/HARDWARE.md`](HARDWARE.md#pin-map--to-be-confirmed-after-wiring) (not from this file: from the robot).
3. `python firmware/tools/pins_from_table.py` checks that table and writes `firmware/include/pins_generated.h`. It refuses on any error. That generated file is the only place that defines `PINS_CONFIRMED`.
4. He commits `pins_generated.h` together with the filled-in table: it is the record of the pins the firmware was built with.

The "Proposed GPIO" numbers below are allowed **only in this document**. No firmware source contains them.

---

## 1. ESP32 / DevKit rules, with sources

Sources used (fetched on 2026-11-17 unless marked):

- **[IDF-GPIO]** ESP-IDF Programming Guide (stable), *API Reference → Peripherals → GPIO & RTC GPIO*, section "GPIO Summary" and `gpio_set_pull_mode` — https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html (read 2026-11-17).
- **[BOOT]** esptool documentation, *Advanced Topics → Boot Mode Selection* (ESP32), sections "GPIO0", "GPIO2", "MTDI / GPIO12", "MTDO / GPIO15" — https://docs.espressif.com/projects/esptool/en/latest/esp32/advanced-topics/boot-mode-selection.html (read 2026-11-17).
- **[IDF-LEDC]** ESP-IDF Programming Guide, *Peripherals → LED Control (LEDC)*, "Introduction" — https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/ledc.html (read 2026-11-17).
- **[IDF-PCNT]** ESP-IDF Programming Guide, *Peripherals → Pulse Counter (PCNT)* — https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/pcnt.html (read 2026-11-17; it does not state the unit count for the ESP32, it refers to the TRM).
- **[DS]** *ESP32 Series Datasheet* (Espressif), section "Strapping Pins" and the peripheral overview (PCNT, LEDC, ADC). The PDF was downloaded on 2026-11-17 but **could not be turned into text here, so it was not re-read today**; the points taken only from it are marked [DS, not re-read].
- **[TRM]** *ESP32 Technical Reference Manual*, chapter "Pulse Count Controller (PCNT)" — **not re-read today**.
- **[ERRATA]** *ESP32 Series SoC Errata*, item 3.11 (GPIO36/GPIO39 glitch) — referred to by [IDF-GPIO]; the errata document itself was **not re-read today**.
- **[WROOM]** *ESP32-WROOM-32 Datasheet* (Espressif), "Pin Definitions" — **not re-read today**; used only for "GPIO6–11 go to the module's flash".

| Rule | What it means here | Source |
|---|---|---|
| **GPIO6–11 are the SPI flash** | Never use them (the DevKit exposes them as CLK, SD0–SD3, CMD; touching them crashes the chip). | [IDF-GPIO] "GPIO6-11 and GPIO16-17 are usually connected to the SPI flash and PSRAM integrated on the module"; [WROOM] |
| **GPIO16/17 and PSRAM** | Free on an ESP32-**WROOM**-32 module (no PSRAM). On an ESP32-**WROVER** module they belong to the PSRAM. **Check the module label on the DevKit** before using 16/17. | [IDF-GPIO] same sentence |
| **GPIO34, 35, 36 (VP), 39 (VN) are input-only, no internal pull-up/down** | Only for inputs that are driven, or have an external pull-up (the BSS138 modules have 10 kΩ pull-ups on both sides). | [IDF-GPIO] "GPIO34-39 can only be set as input mode and do not have software-enabled pullup or pulldown functions" |
| **GPIO36/39 glitch** | Do not use their interrupts with ADC or Wi-Fi/Bluetooth with sleep. Neither is used here (the battery is read by the INA219, Wi-Fi is off), so they are usable. | [IDF-GPIO] → [ERRATA] 3.11 |
| **GPIO1 (TX0) / GPIO3 (RX0) = UART0** | Wired to the DevKit's USB-serial chip: this is the link to the Pi (`docs/SERIAL_PROTOCOL.md`) and the flashing port. Never use them for a signal. | [IDF-GPIO] "TXD & RXD are usually used for flashing and debugging" |
| **Strapping pin GPIO0** | Internal pull-up. **Low at reset = download (bootloader) mode**, high = normal boot. The DevKit's BOOT button pulls it low. Anything that can hold it low at reset stops the robot from booting. | [BOOT] "GPIO0" |
| **Strapping pin GPIO2** | Must be low or floating to enter the bootloader; ignored for a normal boot. Many DevKit clones have an LED on GPIO2 (that LED loads the line). | [BOOT] "GPIO2" |
| **Strapping pin GPIO12 (MTDI)** | Internal pull-down. **High at reset selects 1.8 V for the flash (VDD_SDIO)**; the WROOM-32 flash is 3.3 V, so it browns out: no boot, no flashing. **Never connect anything that can be high at reset.** | [BOOT] "If driven High, flash voltage (VDD_SDIO) is 1.8V not default 3.3V" |
| **Strapping pin GPIO15 (MTDO)** | Internal pull-up. Low at reset only silences the ROM boot messages on UART0 (harmless here, even useful: less noise on the Pi link). | [BOOT] "GPIO15" |
| **Strapping pin GPIO5** | Internal pull-up. Together with GPIO15 it selects the SDIO-slave timing, which only matters for SDIO boot; not used here. Either level is harmless for a normal SPI-flash boot. | [DS, not re-read] "Strapping Pins" table |
| **Pins move during boot** | Until `setup()` runs, the pins are inputs (some strapping pins with their pull-ups); the ROM may also toggle some lines. A motor enable must therefore have its own pull-down (§6). | general; [BOOT] pull-ups above |
| **ADC2 pins (GPIO0, 2, 4, 12, 13, 14, 15, 25, 26, 27)** | ADC2 cannot be used while Wi-Fi is on. No analogue input is planned (battery = INA219 over I2C), and Wi-Fi is not used by the firmware, so this does not matter; they are used here as digital pins. | [IDF-GPIO] "ADC2 pins cannot be used when Wi-Fi is used"; pin list [DS, not re-read] |
| **PCNT: 8 units** | One unit per encoder (channel 0 counts A edges with B as direction, channel 1 counts B edges with A as direction → ×4 quadrature). 4 encoders use units 0–3 (as `src/drivers_esp32/esp32_motion.cpp` already does); 4 units stay free. Any input-capable GPIO can be routed to PCNT through the GPIO matrix. | [IDF-PCNT] "a PCNT unit can act as a quadrature decoder"; unit count [TRM, not re-read] |
| **LEDC: 16 channels (2 groups × 8), 4 timers per group** | Motor PWM left/right on channels 0/1 (one timer, `MOTOR_PWM_HZ`), servo on channel 4 (another timer, 50 Hz) — as in `esp32_drivers.h`. Any output-capable GPIO can carry LEDC. | [IDF-LEDC] "LEDC channels are divided into two groups of 8 channels each"; timers 0–3 |

**Pins the DevKit 38-pin exposes** (ESP32-DevKitC layout, both rows): 3V3, EN, 36 (VP), 39 (VN), 34, 35, 32, 33, 25, 26, 27, 14, 12, GND, 13, 9 (SD2), 10 (SD3), 11 (CMD), 5V — GND, 23, 22, 1 (TX0), 3 (RX0), 21, GND, 19, 18, 5, 17, 16, 4, 0, 2, 15, 8 (SD1), 7 (SD0), 6 (CLK). Clones sometimes label differently: **check against the board in hand.** GPIO 20, 24, 28–31, 37, 38 do not exist on this board; the checker rejects them.

**Resulting budget**

| Group | GPIOs | Count |
|---|---|---|
| Free, input and output, no strapping | 4, 13, 14, 16\*, 17\*, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33 | 15 |
| Input-only | 34, 35, 36, 39 | 4 |
| Strapping, harmless at either level (inputs only) | 5, 15 | 2 |
| Strapping, dangerous | 0 (boot mode), 2 (boot mode, LED), 12 (flash voltage) | 3 |
| Never | 1, 3 (USB link), 6–11 (flash) | 8 |

\* only on a WROOM module (no PSRAM).

## 2. Signal count

| Group | Signals (`pins.h` names) | Direction | Count |
|---|---|---|---|
| Motors (per side: PWM + 2 directions; front and rear channel of a side wired in parallel) | `PIN_MOTOR_LEFT_PWM`, `PIN_MOTOR_LEFT_DIR_A`, `PIN_MOTOR_LEFT_DIR_B`, `PIN_MOTOR_RIGHT_PWM`, `PIN_MOTOR_RIGHT_DIR_A`, `PIN_MOTOR_RIGHT_DIR_B` | out | 6 |
| Encoders (4 × A/B) | `PIN_ENC_FL_A`, `PIN_ENC_FL_B`, `PIN_ENC_RL_A`, `PIN_ENC_RL_B`, `PIN_ENC_FR_A`, `PIN_ENC_FR_B`, `PIN_ENC_RR_A`, `PIN_ENC_RR_B` | in | 8 |
| Ultrasonic (4 × trigger/echo) | `PIN_US_FL_TRIG`, `PIN_US_FL_ECHO`, `PIN_US_FC_TRIG`, `PIN_US_FC_ECHO`, `PIN_US_FR_TRIG`, `PIN_US_FR_ECHO`, `PIN_US_REAR_TRIG`, `PIN_US_REAR_ECHO` | 4 out / 4 in | 8 |
| I2C (ICM-20948, INA219) | `PIN_I2C_SDA`, `PIN_I2C_SCL` | bidirectional | 2 |
| Pan servo | `PIN_SERVO_PAN` | out | 1 |
| **Total** | | 11 out, 12 in, 2 bidirectional | **25** |

With L298N modules the per-side wiring is: left side's motors on one L298N, right side's on the other; **ENA and ENB jumpers removed**, ENA+ENB of that module joined to the side's `PWM` line, IN1+IN3 to `DIR_A`, IN2+IN4 to `DIR_B`. (This keeps the firmware's per-side driving; the parallel wiring must keep both motors of a side turning the same way — check at the first motor test.)

## 3. Option A — everything on GPIO

- Outputs (11) and I2C (2) need input/output pins: 13. Inputs (12): 4 can go on 34/35/36/39, 8 need further pins.
- Pins needed outside the input-only group: 13 + 8 = 21. Available without strapping: 15. **6 short.**
- Adding all five strapping pins gives 15 + 5 = 20, plus 4 input-only = 24 for 25 signals. **Option A does not fit even with every strapping pin**; the 25th signal would need GPIO1 or GPIO3, which is the USB link to the Pi. It is not possible.
- Even a trimmed Option A (one sensor fewer) would put signals on **GPIO0** (an encoder or echo held low at reset → the robot does not boot), **GPIO2** (LED on the line, boot-mode interaction) and **GPIO12** (anything high at reset → flash at 1.8 V → no boot). An encoder's level at reset depends on where the wheel stopped, so these failures would be random. **Rejected.**

## 4. Option B — I2C expander on the existing bus

**Move the 4 HC-SR04 trigger lines** (`PIN_US_*_TRIG`) to an I/O expander on the ICM-20948/INA219 I2C bus. Echoes stay on GPIO (their timing is measured with interrupts).

GPIO needed then: 25 − 4 = 21 = 15 free + 4 input-only + 2 harmless strapping pins (5 and 15, used as **inputs** only). It fits exactly; GPIO0, 2 and 12 stay unused; the expander has 12 spare pins.

**What to buy:** one **MCP23017** breakout (16 I/O, push-pull outputs, I2C up to 400 kHz in standard/fast mode). Power it at **3.3 V** (same as the bus pull-ups). Tie its RESET pin to 3.3 V (through 10 kΩ) unless the module already does.
A PCF8574 (8 I/O) would also do, but its outputs are quasi-bidirectional with a weak high (it can only source about 0.1 mA when high), which is a poor driver for a trigger input; prefer the MCP23017. *(Electrical figures for MCP23017/PCF8574 are from their Microchip/TI datasheets, from memory, not re-read today.)*

**Address:** MCP23017 = 0x20 + (A2 A1 A0). With A0–A2 to GND: **0x20**. No clash with the ICM-20948 (0x68/0x69), the INA219 (0x40–0x4F); a PCF8574 would be 0x20–0x27, a PCF8574A 0x38–0x3F. The I2C scan of the firmware diagnostics (S2c) and `robotctl diag` should then expect 0x20.

**Timing:** one MCP23017 register write at 400 kHz is address + register + data ≈ 3 × 9 bits + start/stop ≈ 75 µs (≈ 300 µs at the firmware's present 100 kHz `I2C_HZ`). The trigger becomes "write high, write low", so the pulse is ≥ 75 µs — the HC-SR04 needs **≥ 10 µs**, so a longer pulse is fine. The distance is measured from the echo edges on GPIO, so the I2C delay does not change the reading; it only shifts when the ping starts. 4 pings × 2 writes per cycle add ≈ 0.6–2.4 ms of bus time per ultrasonic cycle; this must run off the control task (S2.4 already asks for I2C off the control task or with bounded time).

**Safety:** the obstacle stop is still local (PWM to 0 on a GPIO, rule 4). If the I2C bus hangs, no ping is sent, so every sensor returns "no echo"; the firmware already treats missing front sensors as "forward blocked" and a missing rear sensor as "reverse blocked" (`firmware/README.md`, guards). The expander does not hold any motor signal.

**Why not the direction lines on the expander instead?** That also frees 4 GPIOs, but then a motor direction depends on an I2C write in the control path (a failed write = wrong direction while the PWM is on), and after an ESP32 reset the MCP23017 keeps its old outputs. Triggers on the expander fail safe; directions do not.

**What the firmware needs for Option B (not written by this task — see the S1.2 report):** an expander driver (MCP23017 at `SCR_EXPANDER_I2C_ADDR`, outputs low at start), and `Esp32Ultrasonic::trigger()` using it when `SCR_PIN_IS_EXPANDER(pin)` is true. Until the firmware defines `SCR_FIRMWARE_HAS_PIN_EXPANDER`, `pins.h` refuses to compile a table that uses expander pins.

**How the table expresses it:** a trigger row takes `X0` … `X15` in the GPIO column (X0–X7 = MCP23017 port A GPA0–GPA7, X8–X15 = port B GPB0–GPB7), and the row `SCR_EXPANDER_I2C_ADDR` holds the address (`0x20`). The generator writes `#define PIN_US_FL_TRIG (SCR_PIN_EXPANDER_BASE + 0)` (the base is 100, never a GPIO) and `#define SCR_PINS_USE_EXPANDER 1`. Only the four `PIN_US_*_TRIG` rows may use `X` values; the checker refuses `X` on every other row (motor PWM **and direction**, servo, encoder, echo, I2C) — the firmware refuses expander direction pins too, since a direction on I2C fails unsafe — and refuses any `X` value while the address row is empty.

## 5. Recommendation: **Option B (MCP23017 with the 4 trigger lines)**

Option A does not fit: 25 signals need 25 pins and the board has 24 even after spending every strapping pin, including GPIO0 and GPIO12, which can stop the robot from booting at random. Option B with the triggers on an MCP23017 fits exactly (no GPIO left over, but 12 expander pins free for anything added later), uses no dangerous strapping pin (only GPIO5 and GPIO15 as inputs, which are harmless at either level), costs one small module on the bus that already exists, and keeps every motor signal and the stop path on direct GPIO. The cost is one more I2C device and an expander driver in the firmware; a lost bus fails safe (no echoes → driving blocked).

## 6. Level shifting

- **Rover 5 encoders.** The product pages (Pololu 1551 and Core Electronics, read 2026-11-17 through a search) describe the encoder cable as red **+5 V**, black 0 V, yellow and white = quadrature outputs; since 2013 Dagu ships magnetic encoders. **No source found today gives a supply range** (whether they work at 3.3 V). So: plan for a **5 V supply → 8 shifter channels**. Before wiring, measure: power one encoder at 3.3 V on the bench, turn the wheel, check both outputs with a multimeter/scope. Only if they switch cleanly 0 ↔ 3.3 V may the shifters be skipped (and then 34/35/36/39 need external 10 kΩ pull-ups if the outputs are open-collector).
- **HC-SR04 echoes** are 5 V: they need a divider or a shifter. ESP32 inputs are 3.3 V only.
- **The 2 "logic level converter" modules** of the list are BSS138 bidirectional modules with **4 channels each = 8 channels** (assumed from the usual module; check the bought one). Needed with 5 V encoders: 8 (encoders) + 4 (echoes) = **12 → 4 missing.**
- **Proposal:** the 8 BSS138 channels for the 8 encoder lines; the 4 echoes through **resistor dividers** (1 kΩ from echo to the GPIO, 2 kΩ from the GPIO to GND → 5 V × 2/3 = 3.33 V; 4 × 2 resistors, cheap). Alternative: buy a third 4-channel module. Triggers and L298N inputs need no shifting (3.3 V high is above the L298's input-high threshold of 2.3 V; HC-SR04 triggers are commonly driven at 3.3 V — both from memory, **verify at the first ultrasonic and motor tests**).
- I2C: the ICM-20948 and INA219 breakouts run at 3.3 V; the MCP23017 at 3.3 V too. Only one set of bus pull-ups (most breakouts have their own: check that the total is not below ≈ 2 kΩ).

## 7. Boot safety

- **10 kΩ pull-down from each L298N ENA and ENB to GND** (4 resistors). Until the firmware drives the PWM line, the enables are low, so the tracks cannot move while the ESP32 boots, resets, is being flashed or is unplugged. The L298N module's ENA/ENB jumpers (to 5 V) **must be removed**, otherwise the motors run at full speed whenever IN1/IN2 differ.
- **Motor outputs LOW as the very first thing in `setup()`** (S2.4; firmware's job): PWM and direction pins to OUTPUT LOW before the serial port, I2C or anything else.
- None of the motor pins in the proposal is a strapping pin, so no motor signal can change how the chip boots, and the boot-time state of a strapping pin can never reach a motor.
- **No motor line (the six `PIN_MOTOR_*` signals) on GPIO 1, 3, 5, 14 or 15** (firmware safety review, finding 4b). The checker refuses them on those rows. Why, and how sure:
  - GPIO1 / GPIO3 are UART0; the ROM bootloader prints its boot messages on U0TXD (GPIO1) at every reset — **Espressif** ([BOOT], "MTDO / GPIO15": "silences boot messages printed by the ROM bootloader"; [IDF-GPIO] TXD/RXD).
  - GPIO5 and GPIO15 are strapping pins with internal pull-ups at reset, so they sit high until `setup()` runs — **Espressif** ([BOOT] for GPIO15; [DS, not re-read] for GPIO5).
  - GPIO5, GPIO14 and GPIO15 output a short PWM / pulse signal during boot — **commonly reported** by hobbyist pin guides (e.g. Random Nerd Tutorials' "ESP32 Pinout Reference"); **not found in Espressif documents and not verified** here. Treated as true to be safe.
  - The proposal (§8) already puts no motor line on these pins (motors on 25, 26, 27, 33, 32, 23); GPIO14 and GPIO5/15 carry echo inputs only, so **no change to the proposed table was needed**.
- Echoes on GPIO5/GPIO15: the HC-SR04 holds its echo low when idle (and the divider pulls it low when the sensor is unpowered), so at reset GPIO15 is low (ROM log silenced) and GPIO5 is low (SDIO timing, unused). Both are harmless; the table still asks for "yes" on those rows.

## 8. Proposed wiring table

Same columns as the pin map in `docs/HARDWARE.md`, plus "Proposed GPIO". **This is the proposal; the real table in `docs/HARDWARE.md` is filled from the wired robot.**

| Signal | `pins.h` name | Direction (ESP32 side) | Level shifting | Proposed GPIO | ESP32 GPIO | Strapping pin OK? (yes) | Checked by / date |
|---|---|---|---|---|---|---|---|
| Left motors PWM (L298N #1 ENA+ENB, 10 kΩ pull-down) | `PIN_MOTOR_LEFT_PWM` | out | – | 25 | | | |
| Left motors direction A (IN1+IN3) | `PIN_MOTOR_LEFT_DIR_A` | out | – | 26 | | | |
| Left motors direction B (IN2+IN4) | `PIN_MOTOR_LEFT_DIR_B` | out | – | 27 | | | |
| Right motors PWM (L298N #2 ENA+ENB, 10 kΩ pull-down) | `PIN_MOTOR_RIGHT_PWM` | out | – | 33 | | | |
| Right motors direction A (IN1+IN3) | `PIN_MOTOR_RIGHT_DIR_A` | out | – | 32 | | | |
| Right motors direction B (IN2+IN4) | `PIN_MOTOR_RIGHT_DIR_B` | out | – | 23 | | | |
| Encoder front-left A | `PIN_ENC_FL_A` | in | 5 V → 3.3 V (BSS138 #1 ch1) | 34 | | | |
| Encoder front-left B | `PIN_ENC_FL_B` | in | 5 V → 3.3 V (BSS138 #1 ch2) | 35 | | | |
| Encoder rear-left A | `PIN_ENC_RL_A` | in | 5 V → 3.3 V (BSS138 #1 ch3) | 36 | | | |
| Encoder rear-left B | `PIN_ENC_RL_B` | in | 5 V → 3.3 V (BSS138 #1 ch4) | 39 | | | |
| Encoder front-right A | `PIN_ENC_FR_A` | in | 5 V → 3.3 V (BSS138 #2 ch1) | 16 | | | |
| Encoder front-right B | `PIN_ENC_FR_B` | in | 5 V → 3.3 V (BSS138 #2 ch2) | 17 | | | |
| Encoder rear-right A | `PIN_ENC_RR_A` | in | 5 V → 3.3 V (BSS138 #2 ch3) | 18 | | | |
| Encoder rear-right B | `PIN_ENC_RR_B` | in | 5 V → 3.3 V (BSS138 #2 ch4) | 19 | | | |
| I2C data (ICM-20948, INA219, MCP23017) | `PIN_I2C_SDA` | bidirectional | – (3.3 V devices) | 21 | | | |
| I2C clock | `PIN_I2C_SCL` | bidirectional | – (3.3 V devices) | 22 | | | |
| Ultrasonic front-left trigger | `PIN_US_FL_TRIG` | out | – | X0 (MCP23017 GPA0) | | | |
| Ultrasonic front-left echo | `PIN_US_FL_ECHO` | in | divider 1 kΩ / 2 kΩ | 14 | | | |
| Ultrasonic front-centre trigger | `PIN_US_FC_TRIG` | out | – | X1 (GPA1) | | | |
| Ultrasonic front-centre echo | `PIN_US_FC_ECHO` | in | divider 1 kΩ / 2 kΩ | 4 | | | |
| Ultrasonic front-right trigger | `PIN_US_FR_TRIG` | out | – | X2 (GPA2) | | | |
| Ultrasonic front-right echo | `PIN_US_FR_ECHO` | in | divider 1 kΩ / 2 kΩ | 5 (strapping, harmless as input) | | yes | |
| Ultrasonic rear trigger | `PIN_US_REAR_TRIG` | out | – | X3 (GPA3) | | | |
| Ultrasonic rear echo | `PIN_US_REAR_ECHO` | in | divider 1 kΩ / 2 kΩ | 15 (strapping, harmless as input) | | yes | |
| Pan servo signal (MG996R, own 5 A buck, common GND) | `PIN_SERVO_PAN` | out | – | 13 | | | |
| I2C expander (MCP23017) address — Option B only | `SCR_EXPANDER_I2C_ADDR` | – | – | 0x20 | | | |

Unused and left free: GPIO0, 2, 12 (dangerous strapping), 1/3 (USB), 6–11 (flash); MCP23017 X4–X15.

Notes on the choices: encoders on the input-only pins (PCNT needs no pull-ups; the BSS138 module provides them); echoes on interrupt-capable ordinary pins plus the two harmless strapping pins; motors and servo on ordinary output pins; I2C on 21/22 (the Arduino-ESP32 default pins). Any other assignment that passes the checker is equally valid — what counts is the table filled from the robot.

## 9. The generated header is committed

After wiring, `pins_from_table.py` writes `firmware/include/pins_generated.h` with every `PIN_*` value, the table's checksum and its date. **Mohannd commits that file** with the filled-in table: it is the record of exactly which pins the firmware was built with, and a later `--check` run shows whether the table changed since.
