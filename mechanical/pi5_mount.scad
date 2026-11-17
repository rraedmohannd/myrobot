// pi5_mount.scad - Raspberry Pi 5 mount plate with four standoffs.
// NOTHING HERE IS VERIFIED. Every dimension below is nominal or a guess.
// Mounting to the Rover 5 deck: glue / double-sided tape on the plate bottom,
// or zip ties through the four slots. No holes are drilled in the deck.
include <common.scad>

// ---- Board (nominal: Raspberry Pi 5 mechanical drawing, raspberrypi.com) ----
pi_board_x     = 85.0;  // mm; "Raspberry Pi 5 mechanical drawing"             // VERIFY WITH CALIPERS
pi_board_y     = 56.0;  // mm; "Raspberry Pi 5 mechanical drawing"             // VERIFY WITH CALIPERS
pi_hole_dx     = 58.0;  // mm, hole spacing along X; RPi 5 mechanical drawing   // VERIFY WITH CALIPERS
pi_hole_dy     = 49.0;  // mm, hole spacing along Y; RPi 5 mechanical drawing   // VERIFY WITH CALIPERS
pi_hole_edge   = 3.5;   // mm, hole centre to board edge; RPi 5 mech. drawing   // VERIFY WITH CALIPERS
pi_hole_d      = 2.7;   // mm, M2.5 clearance (board hole); RPi 5 drawing       // VERIFY WITH CALIPERS

// ---- Standoffs ----
standoff_od    = 6.0;   // mm; guess                                            // VERIFY WITH CALIPERS
standoff_id    = 2.2;   // mm, M2.5 self-tap pilot; guess (3.2 for heat-set)    // VERIFY WITH CALIPERS
standoff_h     = 8.0;   // mm, clearance under the board (solder joints); guess // VERIFY WITH CALIPERS

// ---- Plate ----
plate_margin   = 6.0;   // mm around the board outline; guess                   // VERIFY WITH CALIPERS
plate_t        = 3.0;   // mm; guess                                            // VERIFY WITH CALIPERS
vent_d         = 30.0;  // mm, airflow cut-out under the SoC; guess             // VERIFY WITH CALIPERS
zip_pitch      = 40.0;  // mm between the two slots of a pair; guess            // VERIFY WITH CALIPERS

plate_x = pi_board_x + 2 * plate_margin;
plate_y = pi_board_y + 2 * plate_margin;

// Hole positions relative to the board centre (holes are near the end
// opposite the USB/Ethernet ports, as on the drawing: x from the left edge).
function hole_pos(i, j) = [
    -pi_board_x / 2 + pi_hole_edge + i * pi_hole_dx,
    -pi_board_y / 2 + pi_hole_edge + j * pi_hole_dy
];

module pi5_mount() {
    difference() {
        rounded_plate(plate_x, plate_y, plate_t);
        // airflow / weight cut-out between the standoffs
        translate([-pi_board_x / 2 + pi_hole_edge + pi_hole_dx / 2, 0, -1])
            cylinder(d = vent_d, h = plate_t + 2);
        // zip-tie slot pairs at both short ends
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (plate_x / 2 - plate_margin / 2), sy * zip_pitch / 2, 0])
                rotate([0, 0, 90]) strap_slot(zip_w * 2, zip_h * 2, plate_t);
        // pilot holes through the plate under the standoffs
        for (i = [0, 1], j = [0, 1])
            translate(concat(hole_pos(i, j), [-1]))
                cylinder(d = standoff_id, h = plate_t + 2);
    }
    for (i = [0, 1], j = [0, 1])
        translate(concat(hole_pos(i, j), [plate_t]))
            standoff(standoff_od, standoff_id, standoff_h);
}

pi5_mount();
