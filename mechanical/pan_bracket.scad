// pan_bracket.scad - MG996R pan bracket (servo cradle, horn up) plus a webcam
// clamp that screws to the servo's round horn.
// NOTHING HERE IS VERIFIED. Every dimension below is nominal or a guess.
// Mounting to the Rover 5 deck: glue / double-sided tape on the cradle base,
// or zip ties through the base slots. The servo screws into the cradle with
// its own 4 flange screws (into printed pilot holes). No deck drilling.
// The webcam model is not fixed: measure ITS clip/foot before printing.
//
// part = "both" (print layout), "cradle" or "clamp"
include <common.scad>

part = "both";

// ---- MG996R (nominal: TowerPro "MG996R" datasheet, 40.7 x 19.7 x 42.9 mm) ----
servo_x        = 40.7;  // mm, body length; TowerPro MG996R datasheet            // VERIFY WITH CALIPERS
servo_y        = 19.7;  // mm, body width; TowerPro MG996R datasheet             // VERIFY WITH CALIPERS
servo_h        = 42.9;  // mm, body height incl. gear top; TowerPro datasheet    // VERIFY WITH CALIPERS
flange_x       = 54.0;  // mm, overall flange length; TowerPro datasheet         // VERIFY WITH CALIPERS
flange_z       = 28.0;  // mm, servo bottom to flange underside; guess           // VERIFY WITH CALIPERS
flange_hole_dx = 49.5;  // mm, flange hole centres along X; TowerPro datasheet   // VERIFY WITH CALIPERS
flange_hole_dy = 10.0;  // mm, flange hole centres along Y; TowerPro datasheet   // VERIFY WITH CALIPERS
flange_pilot_d = 3.0;   // mm, pilot for the M4 / #4 flange screws; guess        // VERIFY WITH CALIPERS
shaft_off_x    = 10.0;  // mm, output shaft from body centre along X; guess      // VERIFY WITH CALIPERS
cable_w        = 8.0;   // mm, cable exit slot width; guess                     // VERIFY WITH CALIPERS
cable_h        = 5.0;   // mm, cable exit slot height; guess                    // VERIFY WITH CALIPERS

// ---- Round horn (the disc horn shipped with most MG996R kits) ----
horn_d         = 24.0;  // mm, round horn diameter; guess                       // VERIFY WITH CALIPERS
horn_hole_pcd  = 14.0;  // mm, pitch circle of the horn's outer holes; guess    // VERIFY WITH CALIPERS
horn_hole_d    = 2.0;   // mm, pilot for the small horn screws; guess           // VERIFY WITH CALIPERS
horn_screw_d   = 6.5;   // mm, access hole for the centre horn screw head; guess // VERIFY WITH CALIPERS
horn_recess    = 2.0;   // mm, recess depth that keys the horn; guess           // VERIFY WITH CALIPERS

// ---- Webcam clip / foot (model unknown) ----
cam_clip_w     = 30.0;  // mm, width of the webcam's foot/clip to clamp; guess  // VERIFY WITH CALIPERS
cam_clip_d     = 30.0;  // mm, depth of the foot/clip; guess                    // VERIFY WITH CALIPERS
jaw_h          = 12.0;  // mm, jaw height; guess                                // VERIFY WITH CALIPERS
jaw_t          = 3.0;   // mm, jaw wall thickness; guess                        // VERIFY WITH CALIPERS
pinch_d        = 3.4;   // mm, M3 pinch-screw clearance across the jaws; guess  // VERIFY WITH CALIPERS
tripod_d       = 6.6;   // mm, 1/4"-20 screw clearance (if the cam has a thread); nominal 1/4" // VERIFY WITH CALIPERS

// ---- Cradle ----
wall_t         = 3.0;   // mm; guess                                            // VERIFY WITH CALIPERS
base_t         = 3.0;   // mm; guess                                            // VERIFY WITH CALIPERS
base_margin    = 10.0;  // mm, base wider than the frame for glue area; guess   // VERIFY WITH CALIPERS
clamp_base_t   = 4.0;   // mm, clamp platform thickness; guess                  // VERIFY WITH CALIPERS

pocket_x = servo_x + 2 * fit_clearance;
pocket_y = servo_y + 2 * fit_clearance;
frame_x  = max(flange_x, pocket_x + 2 * wall_t);
frame_y  = pocket_y + 2 * wall_t;

module cradle() {
    difference() {
        union() {
            rounded_plate(frame_x + 2 * base_margin, frame_y + 2 * base_margin, base_t);
            translate([0, 0, base_t]) rounded_plate(frame_x, frame_y, flange_z, 1);
        }
        // servo body pocket (servo bottom rests on the base)
        translate([-pocket_x / 2, -pocket_y / 2, base_t])
            cube([pocket_x, pocket_y, flange_z + 1]);
        // flange screw pilots
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * flange_hole_dx / 2, sy * flange_hole_dy / 2, base_t + 4])
                cylinder(d = flange_pilot_d, h = flange_z);
        // cable exit at the -X end (the end away from the output shaft)
        translate([-frame_x / 2 - 1, -cable_w / 2, base_t])
            cube([frame_x / 2, cable_w, cable_h]);
        // zip-tie slots in the base margin
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (frame_x / 2 + base_margin / 2), sy * (frame_y / 2), 0])
                rotate([0, 0, 90]) strap_slot(zip_w * 2, zip_h * 2, base_t);
    }
}

module clamp() {
    plat_x = max(horn_d + 6, cam_clip_d + 2 * jaw_t);
    plat_y = cam_clip_w + 2 * jaw_t;
    difference() {
        union() {
            rounded_plate(plat_x, plat_y, clamp_base_t);
            // two jaws along X, the foot slides in between them
            for (s = [-1, 1])
                translate([-plat_x / 2, s * (cam_clip_w / 2 + jaw_t / 2) - jaw_t / 2, 0])
                    cube([plat_x, jaw_t, clamp_base_t + jaw_h]);
        }
        // horn key recess on the underside
        translate([0, 0, -1]) cylinder(d = horn_d + 2 * fit_clearance, h = horn_recess + 1);
        // horn screw holes
        for (a = [0 : 90 : 270])
            rotate([0, 0, a]) translate([horn_hole_pcd / 2, 0, -1])
                cylinder(d = horn_hole_d, h = clamp_base_t + 2);
        // centre access for the horn screw / screwdriver, doubles as 1/4" hole
        translate([0, 0, -1]) cylinder(d = max(horn_screw_d, tripod_d), h = clamp_base_t + 2);
        // pinch screws across the jaws
        for (sx = [-1, 1])
            translate([sx * plat_x / 4, 0, clamp_base_t + jaw_h / 2])
                rotate([90, 0, 0]) cylinder(d = pinch_d, h = plat_y + 2, center = true);
        // zip-tie slots as an alternative to the pinch screws
        for (sx = [-1, 1])
            translate([sx * (plat_x / 2 - zip_w), 0, 0])
                rotate([0, 0, 90]) strap_slot(zip_w * 2, zip_h * 2, clamp_base_t);
    }
}

echo(str("output shaft offset from cradle centre (X) = ", shaft_off_x, " mm (guess)"));

if (part == "cradle" || part == "both") cradle();
if (part == "clamp" || part == "both")
    translate([0, frame_y / 2 + base_margin + cam_clip_w / 2 + jaw_t + 8, 0]) clamp();
