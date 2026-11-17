"""Write an invisible Xcursor theme (every cursor is one transparent pixel).

    python3 robot/deploy/make_invisible_cursor.py ~/.local/share/icons/scr-invisible

Used by robot/deploy/kiosk-setup.sh for the labwc (Wayland) session of the robot screen: labwc
has no "hide cursor" option, but it draws the cursor from the theme named in XCURSOR_THEME
(~/.config/labwc/environment). The X11 fallback session uses `unclutter` instead.
Standard library only; the file format is the Xcursor format (libXcursor, "Xcur" files).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

IMAGE_TYPE = 0xFFFD0002
NOMINAL_SIZE = 24
# CSS / cursor-shape-v1 names and the classic X11 names, so every shape Chromium asks for is blank
NAMES = """
default left_ptr arrow top_left_arrow pointer hand hand1 hand2 pointing_hand text xterm ibeam
vertical-text wait watch progress left_ptr_watch half-busy crosshair cross tcross cell
move fleur all-scroll grab openhand grabbing closedhand dnd-move dnd-copy dnd-link dnd-none
not-allowed no-drop forbidden crossed_circle help question_arrow context-menu copy alias
col-resize sb_h_double_arrow row-resize sb_v_double_arrow n-resize s-resize e-resize w-resize
ne-resize nw-resize se-resize sw-resize ew-resize ns-resize nesw-resize nwse-resize
top_side bottom_side left_side right_side top_left_corner top_right_corner
bottom_left_corner bottom_right_corner size_hor size_ver size_bdiag size_fdiag
zoom-in zoom-out
""".split()


def xcursor_blank() -> bytes:
    """One 1x1 fully transparent image, nominal size 24, hotspot 0,0."""
    header = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, 1)
    position = 16 + 12  # file header + one table-of-contents entry
    toc = struct.pack("<III", IMAGE_TYPE, NOMINAL_SIZE, position)
    image = struct.pack("<IIIIIIIII", 36, IMAGE_TYPE, NOMINAL_SIZE, 1, 1, 1, 0, 0, 0)
    pixel = struct.pack("<I", 0x00000000)  # ARGB, alpha 0
    return header + toc + image + pixel


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    theme = Path(argv[0]).expanduser()
    cursors = theme / "cursors"
    cursors.mkdir(parents=True, exist_ok=True)
    (theme / "index.theme").write_text(
        "[Icon Theme]\nName=scr-invisible\nComment=Invisible cursor (smart-campus-robot kiosk)\n",
        encoding="ascii",
    )
    data = xcursor_blank()
    for name in NAMES:
        (cursors / name).write_bytes(data)
    print(f"{theme}: {len(NAMES)} blank cursors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
