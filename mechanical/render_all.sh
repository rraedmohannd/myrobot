#!/bin/sh
# Runs INSIDE the openscad/openscad container (called by build.sh / build.ps1).
# For each part: export the STL, then render a PNG preview of that STL.
# The image's xvfb-run hangs under Docker (it waits for a signal from Xvfb that
# never arrives), so a plain Xvfb is started by hand instead.
set -u
cd /work
mkdir -p out
Xvfb :99 -screen 0 1280x1024x24 >/dev/null 2>&1 &
XPID=$!
sleep 2
export DISPLAY=:99

fail=0
# name|scad file|extra -D argument (one, optional)
for entry in \
  "display_bracket|display_bracket.scad|" \
  "pan_bracket|pan_bracket.scad|" \
  "hcsr04_holder|hcsr04_holder.scad|" \
  "hcsr04_holder_yaw25|hcsr04_holder.scad|yaw_deg=25" \
  "pi5_mount|pi5_mount.scad|" \
  "battery_plate|battery_plate.scad|"
do
  name=$(echo "$entry" | cut -d'|' -f1)
  scad=$(echo "$entry" | cut -d'|' -f2)
  def=$(echo "$entry" | cut -d'|' -f3)
  echo "== $name"
  if [ -n "$def" ]; then set -- -D "$def"; else set --; fi
  if openscad "$@" -o "out/$name.stl" "$scad" >"out/$name.log" 2>&1; then
    echo "   STL ok ($(wc -c <"out/$name.stl") bytes)"
  else
    echo "   STL FAILED (see out/$name.log)"; fail=1; continue
  fi
  grep -E "^(WARNING|ECHO)" "out/$name.log" | sed 's/^/   /'
  printf 'color("SteelBlue") import("%s");\n' "$name.stl" >"out/_view_$name.scad"
  if openscad --imgsize=800,600 --autocenter --viewall \
       --colorscheme=Tomorrow -o "out/$name.png" "out/_view_$name.scad" >>"out/$name.log" 2>&1; then
    echo "   PNG ok ($(wc -c <"out/$name.png") bytes)"
    rm -f "out/$name.log"
  else
    echo "   PNG FAILED (STL is still usable; see out/$name.log)"; fail=1
  fi
  rm -f "out/_view_$name.scad"
done
kill "$XPID" 2>/dev/null
exit $fail
