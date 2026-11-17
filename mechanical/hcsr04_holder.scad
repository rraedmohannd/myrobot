// hcsr04_holder.scad - holder for one HC-SR04 ultrasonic module.
// NOTHING HERE IS VERIFIED. Every dimension below is nominal or a guess.
//
// One part, printed 4 times:
//   front-left   yaw_deg = +25   (or print the 0 part and glue it turned 25 deg)
//   front-centre yaw_deg =   0
//   front-right  yaw_deg = -25   (the mirror of +25; set yaw_deg = -25)
//   rear         yaw_deg =   0
// yaw_deg turns the sensor face relative to the base's straight edge, so the
// base can be glued square to the deck edge and the sensor still points at
// the angle. Render a variant with:  openscad -D yaw_deg=25 ...
// Mounting to the Rover 5 deck: glue / double-sided tape, or a zip tie through
// the base slot. The module is held by its two transducer cans in the face
// holes plus a zip tie through the face slots. No screws, no drilling.
include <common.scad>

yaw_deg      = 0;      // deg; -25 / 0 / +25 front, 0 rear (docs/HARDWARE.md layout)

// ---- HC-SR04 (nominal: ElecFreaks "HC-SR04 Ultrasonic Ranging Module" user guide, 45x20x15 mm) ----
pcb_x        = 45.0;   // mm; ElecFreaks HC-SR04 user guide                     // VERIFY WITH CALIPERS
pcb_y        = 20.0;   // mm; ElecFreaks HC-SR04 user guide                     // VERIFY WITH CALIPERS
pcb_t        = 1.6;    // mm; guess (standard FR4)                              // VERIFY WITH CALIPERS
can_d        = 16.0;   // mm, transducer can diameter; guess (clones vary)     // VERIFY WITH CALIPERS
can_pitch    = 26.0;   // mm, centre-to-centre of the two cans; guess          // VERIFY WITH CALIPERS
can_h        = 12.0;   // mm, can height above the PCB; guess                  // VERIFY WITH CALIPERS
pin_clear_z  = 4.0;    // mm, header pins stick out of the bottom edge; guess   // VERIFY WITH CALIPERS

// ---- Holder ----
face_t       = 3.0;    // mm, front face thickness (cans pass through); guess   // VERIFY WITH CALIPERS
face_margin  = 3.0;    // mm around the PCB outline; guess                      // VERIFY WITH CALIPERS
base_d       = 30.0;   // mm, base depth (glue area); guess                     // VERIFY WITH CALIPERS
base_t       = 3.0;    // mm; guess                                             // VERIFY WITH CALIPERS
sensor_z     = 8.0;    // mm, PCB bottom edge above the deck (pin clearance); guess // VERIFY WITH CALIPERS
gusset_t     = 3.0;    // mm; guess                                             // VERIFY WITH CALIPERS

face_x = pcb_x + 2 * face_margin;
face_h = sensor_z + pcb_y + face_margin;
base_x = face_x / cos(yaw_deg) + 10;   // wide enough to carry the turned face
base_front = face_x / 2 * abs(sin(yaw_deg)) + 2;  // base reaches the forward face corner

module face() {
    // XZ plate, front surface at y = 0, sensor looks toward -Y
    difference() {
        translate([-face_x / 2, 0, 0]) cube([face_x, face_t, face_h]);
        // transducer holes
        for (s = [-1, 1])
            translate([s * can_pitch / 2, -1, sensor_z + pcb_y / 2])
                rotate([-90, 0, 0]) cylinder(d = can_d + 2 * fit_clearance, h = face_t + 2);
        // zip-tie slots above and below the cans, tie goes around the PCB
        for (s = [-1, 1])
            translate([0, face_t / 2, sensor_z + pcb_y / 2 + s * (pcb_y / 2 + 1.5)])
                cube([zip_w * 2, face_t + 2, zip_h], center = true);
    }
    // side gussets
    for (s = [-1, 1])
        translate([s * (face_x / 2 - gusset_t / 2) - gusset_t / 2, face_t, 0])
            rotate([90, 0, 90])
                linear_extrude(gusset_t)
                    polygon([[0, 0], [base_d - face_t, 0], [0, face_h * 0.6]]);
}

module hcsr04_holder() {
    difference() {
        union() {
            // base, its front edge along X is the edge glued square to the deck edge
            translate([-base_x / 2, -base_front, 0]) cube([base_x, base_d + base_front, base_t]);
            rotate([0, 0, yaw_deg]) face();
        }
        // base zip-tie slot
        translate([0, base_d - 6, 0]) strap_slot(zip_w * 2, zip_h * 2, base_t);
        // keep the face from poking below the deck
        translate([-200, -200, -50]) cube([400, 400, 50]);
    }
}

hcsr04_holder();
