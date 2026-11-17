# Render every part to out\<part>.stl and an out\<part>.png preview, using
# OpenSCAD in Docker (official image openscad/openscad:2021.01, Docker Hub).
# Usage: powershell -File mechanical\build.ps1
# The per-part work is in render_all.sh, which runs inside the container.
$image = if ($env:OPENSCAD_IMAGE) { $env:OPENSCAD_IMAGE } else { 'openscad/openscad:2021.01' }
$here = $PSScriptRoot
& docker run --rm -v "${here}:/work" -w /work $image sh /work/render_all.sh
exit $LASTEXITCODE
