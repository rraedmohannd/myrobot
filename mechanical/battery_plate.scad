// battery_plate.scad - plate for a 3S (3 x 18650, side by side) battery holder,
// with a low locating rim and slots for two hook-and-loop (velcro) straps.
// NOTHING HERE IS VERIFIED. Every dimension below is nominal or a guess.
// The holder model is not chosen yet: measure YOUR holder first.
// Mounting to the Rover 5 deck: glue / double-sided tape, or zip ties through
// the corner slots. No holes are drilled in the deck.
include <common.scad>

// ---- 18650 cell (nominal: IEC 61960 "18650" designation, 18 mm x 65 mm) ----
cell_d        = 18.6;  // mm; 18650 nominal, typical datasheet max              // VERIFY WITH CALIPERS
cell_l        = 65.2;  // mm; 18650 nominal, typical datasheet max (flat top)    // VERIFY WITH CALIPERS
cells         = 3;     // 3S, side by side (from docs/HARDWARE.md)

// ---- Holder outline (no drawing; generic 3-slot plastic holder) ----
holder_x      = 78.0;  // mm, along the cells (cell + springs); guess            // VERIFY WITH CALIPERS
holder_y      = 60.0;  // mm, across the cells (3 x ~20 mm); guess               // VERIFY WITH CALIPERS
holder_h      = 21.0;  // mm, holder height (for strap length only); guess      // VERIFY WITH CALIPERS

// ---- Plate ----
plate_t       = 3.0;   // mm; guess                                             // VERIFY WITH CALIPERS
rim_h         = 4.0;   // mm, locating rim height above plate; guess            // VERIFY WITH CALIPERS
rim_w         = 2.4;   // mm, rim wall width; guess                             // VERIFY WITH CALIPERS
strap_w       = 21.0;  // mm, slot length for a 20 mm velcro strap; guess       // VERIFY WITH CALIPERS
strap_t       = 3.0;   // mm, slot width for the strap thickness; guess        // VERIFY WITH CALIPERS
strap_pitch   = 40.0;  // mm between the two straps (along the cells); guess    // VERIFY WITH CALIPERS
slot_margin   = 5.0;   // mm from rim to strap slot; guess                      // VERIFY WITH CALIPERS
wire_gap      = 14.0;  // mm, gap in the rim at one end for leads/BMS; guess    // VERIFY WITH CALIPERS
corner_zip_in = 5.0;   // mm, corner zip-tie slot inset from the plate edge; guess // VERIFY WITH CALIPERS

pocket_x = holder_x + 2 * fit_clearance;
pocket_y = holder_y + 2 * fit_clearance;
plate_x  = pocket_x + 2 * rim_w + 2 * corner_zip_in + 6;
plate_y  = pocket_y + 2 * rim_w + 2 * (slot_margin + strap_t) + 4;

module battery_plate() {
    difference() {
        union() {
            rounded_plate(plate_x, plate_y, plate_t);
            // locating rim
            difference() {
                rounded_plate(pocket_x + 2 * rim_w, pocket_y + 2 * rim_w, plate_t + rim_h, 1);
                translate([0, 0, plate_t]) rounded_plate(pocket_x, pocket_y, rim_h + 1, 0.5);
                // lead / BMS wire exit at the +X end
                translate([pocket_x / 2 - 1, -wire_gap / 2, plate_t])
                    cube([rim_w + 2, wire_gap, rim_h + 1]);
            }
        }
        // strap slots on both long sides, straps run across the cells
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * strap_pitch / 2,
                       sy * (pocket_y / 2 + rim_w + slot_margin + strap_t / 2), 0])
                strap_slot(strap_w, strap_t, plate_t);
        // corner zip-tie slots for fixing the plate to the deck
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (plate_x / 2 - corner_zip_in), sy * (plate_y / 2 - corner_zip_in - 4), 0])
                rotate([0, 0, 90]) strap_slot(zip_w * 2, zip_h * 2, plate_t);
    }
}

// echo for the strap length: perimeter over the holder plus the plate
echo(str("strap length guess (mm) = ", 2 * (holder_h + plate_t) + holder_y + 40));

battery_plate();
