# Follow-Up Protocol — Revision 2 change log (2 Nov 2026)

## Corrections applied
- **Motor drivers:** 2 × L298N withdrawn as final choice (2–4 V bridge drop, 2 A/channel < ≈2.4 A stall). Provisional: 2 × dual VNH MOSFET shields (MikroElectron me-2981), pending current, thermal and 3.3 V logic tests. Hardware table, architecture figure, budget and text updated.
- **Power distribution** section + new Figure 3: 3S pack range, fused branches, PWM-limited motor supply (no direct 3S-to-7.2 V connection), 5 V Pi branch, 6 V servo branch, display via USB, INA219, star ground, proposed 9.9 V cut-off.
- **Encoders:** now “four quadrature encoders; sensor type, resolution and interface to be verified”; count convention (×4 quadrature counts per sprocket revolution) and level shifting explained.
- **Budget:** battery pack + charger, speaker amplifier, mounting kit and ultrasonic allowance added to the base before contingency. Base JOD 327.15 + 25% = **JOD 408.94** (was 302). Separate lines for new purchases, university equipment, total hardware value and recurring costs. Itemised BOM with SKUs, quantities, unit prices and check date (Table 4).
- **Commercial comparison:** “one tenth of the cost” removed; replaced with “low additional procurement cost through reuse of university equipment”; temi price dated and caveated.
- **Odometry/return:** drift and slip limits stated; four-encoder → left/right conversion and mismatch detection added; waypoints described as estimated fallback only; return = direct when feasible, waypoint alternative, stop and report when blocked.
- **Return tests:** route length, turns, floor, payload, repetitions, reference point, tape measurement, heading, success/blockage/failure definitions, reported statistics.
- **Fail-safe:** serial loss, Pi loss, backend/Wi-Fi loss and AI failure separated; proposed timeout 300 ms, stop ≤ 0.5 s, test speed 0.15 m/s; ultrasonic blind-zone limitation stated.
- **Claims:** competitor “No”→“Not verified in reviewed sources”; project “Yes”→“Planned; to be validated”; “fully open”→“student-developed, modifiable application software”; grounding and Jordanian voice presented as design targets.
- **Procurement/completion wording:** “proposed for local procurement”; conclusion and last slide separate done / provisional / next.
- **Existing work:** new table and slide distinguishing code exists / reviewed / executed / no measurements.
- **Market:** IFR figures labelled global context; local demand marked as not yet surveyed.
- **Privacy:** what is stored and not stored; “session-based logs without intentional identity recognition”.
- **Format:** real TOC field (updated), 15 pages, figures resized, references made specific.
- **Slides:** 13 slides; added concept layout, hardware status and existing-work slides; simplified, larger architecture and Gantt.

## Facts verified (sources)
- Rover 5 reference specs (Pololu 1550/1551): 7.2 V motors, 2.4 A stall, 210 mA free run, 1000 counts / 3 wheel revs counting both edges of both channels, 5 V encoder supply.
- MikroElectron listings and prices checked 2 Nov 2026 (all BOM items, incl. Rover 5 4-motor version qty 5, Waveshare 7" qty 1).
- temi V3 Wi-Fi US$ 4,499 excl. tariffs (RobotLAB, 2 Nov 2026).
- Earlier Pi work: files and shell history on the June 2026 card (code exists, reviewed, executed per history; no measurements).

## Still awaiting evidence
- Encoder sensor type, output levels and resolution on the supplied unit.
- Measured motor running/stall current; driver heating; driver logic threshold at 3.3 V.
- Battery pack (me-5914) continuous discharge rating.
- University confirmation for borrowed ultrasonic sensors and 3D printing.
- Type of the existing speaker (USB vs analogue).
- Prices of terminal blocks, motor wire, ties/tape.
- Official submission date (buffer week).
- Supervisor approval of all proposed numerical targets.
