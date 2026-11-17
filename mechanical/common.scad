// common.scad - shared helpers for the Smart Campus Robot printed parts.
// NOTHING IN THIS DIRECTORY IS VERIFIED. No dimension has been measured.
// Mounting to the Rover 5 deck is by glue / double-sided tape / zip ties only.

$fn = 48;

// Global print/fit values (apply to every part that includes this file)
fit_clearance = 0.3;   // mm, added to pockets for a slip fit; guess (typical FDM) // VERIFY WITH CALIPERS (test print)
zip_w         = 4.0;   // mm, zip-tie slot width; guess for 3.6 mm ties          // VERIFY WITH CALIPERS
zip_h         = 2.0;   // mm, zip-tie slot height; guess for 1.1-1.5 mm ties     // VERIFY WITH CALIPERS
corner_r      = 2.0;   // mm, outer corner radius; cosmetic choice

// Rounded rectangle plate, origin at the centre of the bottom face.
module rounded_plate(x, y, h, r = corner_r) {
    rr = min(r, x / 2 - 0.01, y / 2 - 0.01);
    translate([-x / 2 + rr, -y / 2 + rr, 0])
        linear_extrude(h)
            offset(r = rr) square([x - 2 * rr, y - 2 * rr]);
}

// Through-slot for a zip tie / strap, centred at origin, long axis along X.
module strap_slot(len, wid, h) {
    translate([0, 0, -1])
        linear_extrude(h + 2)
            hull() {
                translate([-(len - wid) / 2, 0]) circle(d = wid);
                translate([ (len - wid) / 2, 0]) circle(d = wid);
            }
}

// Pair of zip-tie slots, spaced `pitch` apart along Y, cut through height h.
module zip_pair(pitch, h) {
    for (s = [-1, 1])
        translate([0, s * pitch / 2, 0]) strap_slot(zip_w * 2, zip_h * 2, h);
}

// Simple round standoff with a hole (for self-tapping or heat-set insert).
module standoff(od, id, h) {
    difference() {
        cylinder(d = od, h = h);
        translate([0, 0, -1]) cylinder(d = id, h = h + 2);
    }
}
