// display_bracket.scad - tilted stand for the Waveshare 7" HDMI LCD (C), 1024x600.
// Two small corner stands (left + right, mirror images) instead of one 170 mm
// wide part, so they fit any printer bed. Each stand: a foot, a back rest
// tilted back by tilt_deg, a bottom ledge with a front lip, and a side hook.
// The panel sits on the two ledges and leans on the two rests; a zip tie or a
// velcro strap through the rest slots holds it.
// NOTHING HERE IS VERIFIED. Every dimension below is nominal or a guess.
// The panel's own mounting holes are NOT used (their positions are not
// confirmed). Mounting to the Rover 5 deck: glue / double-sided tape under the
// feet, or zip ties through the foot slots. No deck drilling.
// Check where the panel's HDMI / USB connectors exit before choosing
// stand_inset: the stands must not cover them.
//
// part = "both" (print layout), "left" or "right"
include <common.scad>

part = "both";

// ---- Panel (nominal: Waveshare wiki "7inch HDMI LCD (C)" dimension drawing) ----
panel_w      = 164.9;  // mm, outline width; Waveshare 7inch HDMI LCD (C) drawing  // VERIFY WITH CALIPERS
panel_h      = 100.0;  // mm, outline height; Waveshare 7inch HDMI LCD (C) drawing // VERIFY WITH CALIPERS
panel_t      = 8.0;    // mm, thickest point at the edges (glass+PCB); guess      // VERIFY WITH CALIPERS
bezel_clear  = 4.0;    // mm, lip may cover this much of the bottom bezel; guess  // VERIFY WITH CALIPERS

// ---- Stand ----
tilt_deg     = 15;     // deg back from vertical; guess for a standing visitor   (adjust after a try-out)
stand_w      = 20.0;   // mm, width of each stand along the panel edge; guess     // VERIFY WITH CALIPERS
stand_inset  = 0.0;    // mm, stand outer face in from the panel corner; guess    // VERIFY WITH CALIPERS
rest_t       = 4.0;    // mm, back rest thickness; guess                         // VERIFY WITH CALIPERS
rest_l       = 70.0;   // mm, back rest length (fraction of panel_h); guess      // VERIFY WITH CALIPERS
ledge_t      = 4.0;    // mm, ledge thickness under the panel; guess             // VERIFY WITH CALIPERS
lip_t        = 3.0;    // mm, front lip thickness; guess                         // VERIFY WITH CALIPERS
ledge_z      = 10.0;   // mm, panel bottom edge above the deck; guess            // VERIFY WITH CALIPERS
hook_t       = 3.0;    // mm, side hook thickness; guess                         // VERIFY WITH CALIPERS
hook_h       = 30.0;   // mm, side hook height; guess                            // VERIFY WITH CALIPERS
foot_d       = 70.0;   // mm, foot depth (front-back, glue area); guess          // VERIFY WITH CALIPERS
foot_t       = 4.0;    // mm; guess                                              // VERIFY WITH CALIPERS
strap_len    = 12.0;   // mm, slot for a zip tie / 10 mm velcro; guess           // VERIFY WITH CALIPERS

// Stand in the "panel frame": panel front face is the plane y = 0, panel bottom
// edge at z = 0, panel occupies y in [0, panel_t]. X is along the panel edge,
// the outer (corner) side of the stand is at x = 0, the stand spans x in [0, w].
module stand_panel_frame(w) {
    total_w = w + hook_t;
    difference() {
        union() {
            // back rest behind the panel
            translate([-hook_t, panel_t, -ledge_t]) cube([total_w, rest_t, rest_l + ledge_t]);
            // ledge under the panel
            translate([-hook_t, -lip_t, -ledge_t]) cube([total_w, panel_t + rest_t + lip_t, ledge_t]);
            // front lip (stays within the bottom bezel)
            translate([-hook_t, -lip_t, 0]) cube([total_w, lip_t, bezel_clear]);
            // side hook at the outer corner
            translate([-hook_t, -lip_t, 0]) cube([hook_t, panel_t + lip_t, hook_h]);
        }
        // strap slots through the back rest
        for (z = [rest_l * 0.35, rest_l * 0.8])
            translate([w / 2, panel_t + rest_t / 2, z])
                cube([zip_w * 1.5, rest_t + 2, strap_len], center = true);
    }
}

// Tilted and lifted onto the foot. Tilt: top of the panel moves toward +Y.
module tilted(w) {
    translate([0, 0, foot_t + ledge_z]) rotate([-tilt_deg, 0, 0]) stand_panel_frame(w);
}

module stand(w) {
    // y of the front of the ledge after tilting is about -lip_t; foot starts there
    foot_y0 = -lip_t - 8;
    difference() {
        union() {
            tilted(w);
            // foot
            translate([-hook_t, foot_y0, 0]) cube([w + hook_t, foot_d, foot_t]);
            // fill under the tilted ledge
            hull() {
                translate([-hook_t, foot_y0 + 6, 0]) cube([w + hook_t, panel_t + rest_t + lip_t, foot_t]);
                intersection() {
                    tilted(w);
                    translate([-50, -50, 0]) cube([200, 200, foot_t + ledge_z]);
                }
            }
            // diagonal brace from the foot's back end to the rest
            hull() {
                translate([w / 2 - 2, foot_y0 + foot_d - 6, 0]) cube([4, 6, foot_t]);
                translate([0, 0, foot_t + ledge_z]) rotate([-tilt_deg, 0, 0])
                    translate([w / 2 - 2, panel_t, rest_l * 0.6]) cube([4, rest_t, 6]);
            }
        }
        // foot zip-tie slots
        for (yy = [foot_d * 0.55, foot_d * 0.85])
            translate([w / 2, foot_y0 + yy, 0]) strap_slot(zip_w * 2, zip_h * 2, foot_t);
        // flatten the bottom
        translate([-100, -100, -50]) cube([300, 300, 50]);
    }
}

echo(str("panel top above deck (approx) = ",
         foot_t + ledge_z + panel_h * cos(tilt_deg), " mm"));
echo(str("stand centres along the panel edge = ",
         panel_w - 2 * stand_inset - stand_w, " mm apart"));

if (part == "left"  || part == "both") stand(stand_w);
if (part == "right" || part == "both")
    translate([2 * stand_w + 20, 0, 0]) mirror([1, 0, 0]) stand(stand_w);
