#!/usr/bin/env bash
# Install test of the Raspberry Pi setup WITHOUT a Raspberry Pi.
#
# Starts an arm64 Debian container of the same release as Raspberry Pi OS (trixie), emulated by
# QEMU / binfmt on Docker Desktop, with the repository mounted READ-ONLY. Inside, it copies
# robot/ and shared/ to a work directory, runs `robot/deploy/setup.sh --container-test`, then:
#   1. the robot test suite (python -m pytest -q in robot/)
#   2. robotd --sim for 20 s: /local/status must answer and the log must say SIMULATED
#   3. visiond on a synthetic video made in the container (scripts/synthetic_video_test.py)
#   4. python -c "import mediapipe, cv2"
# and prints the time of each phase.
#
# An emulated arm64 container is NOT a Raspberry Pi: no Pi kernel, no camera, no serial port,
# no GPU, and every timing is the emulator's (much slower than a Pi, and not comparable).
#
# Usage (Git Bash on Windows, or Linux):  bash robot/scripts/container_test.sh
#   The Docker volume scr-arm64-pip-cache keeps downloaded wheels between runs.
#   RELEASE=bookworm bash robot/scripts/container_test.sh tries another Debian release.
# The phase functions below are called through phase():
# shellcheck disable=SC2329
set -euo pipefail

RELEASE="${RELEASE:-trixie}"
IMAGE="debian:$RELEASE"

if [[ "${1:-}" != "--inside" ]]; then
    # ---------------------------------------------------------------- host side
    REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    if command -v cygpath >/dev/null 2>&1; then REPO="$(cygpath -m "$REPO")"; fi
    export MSYS_NO_PATHCONV=1
    echo "== emulation check: docker run --platform linux/arm64 $IMAGE uname -m"
    arch="$(docker run --rm --platform linux/arm64 "$IMAGE" uname -m)"
    if [[ "$arch" != aarch64 ]]; then
        echo "!! arm64 emulation does not work here (uname -m = $arch)" >&2
        exit 1
    fi
    echo "   $arch (emulated)"
    exec docker run --rm --platform linux/arm64 \
        -v "$REPO:/src:ro" \
        -v scr-arm64-pip-cache:/root/.cache/pip \
        -e PIP_CACHE_DIR=/root/.cache/pip \
        "$IMAGE" bash -c 'cp /src/robot/scripts/container_test.sh /tmp/ct.sh && exec bash /tmp/ct.sh --inside'
fi

# -------------------------------------------------------------------- container side
WORK=/work/smart-campus-robot
declare -a NAMES=() TIMES=() RESULTS=()
OVERALL=0

phase() { # phase <name> <command...>
    local name="$1"; shift
    local start=$SECONDS rc=0
    echo
    echo "######## PHASE: $name"
    "$@" || rc=$?
    NAMES+=("$name"); TIMES+=("$((SECONDS - start))")
    if [[ $rc -eq 0 ]]; then RESULTS+=(PASS); else RESULTS+=("FAIL($rc)"); OVERALL=1; fi
    echo "######## $name: ${RESULTS[-1]} in $((SECONDS - start)) s"
}

copy_repo() {
    mkdir -p "$WORK"
    # only what the robot and its tests need (some tests compare with the firmware sources and
    # the backend's page scripts); no Windows virtualenv, no caches, no model files (downloaded)
    tar -C /src -cf - \
        --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
        --exclude='.ruff_cache' --exclude='.pio' --exclude='*.onnx' --exclude='*.task' \
        robot shared firmware backend/app/static | tar -C "$WORK" -xf -
}

setup() { bash "$WORK/robot/deploy/setup.sh" --container-test; }

robot_tests() {
    cd "$WORK/robot"
    .venv/bin/python -m pytest -q -rs -p no:cacheprovider 2>&1 | tee /tmp/pytest.log
    local rc=${PIPESTATUS[0]}
    echo "pytest summary: $(tail -n 1 /tmp/pytest.log)"
    return "$rc"
}

robotd_sim() {
    cd "$WORK/robot"
    local log=/tmp/robotd.log ok=0
    BACKEND_URL="ws://127.0.0.1:9/ws/robot" .venv/bin/python -m robotd --sim >"$log" 2>&1 &
    local pid=$!
    local status=""
    for _ in $(seq 1 20); do
        sleep 1
        if status="$(curl -fsS --max-time 3 http://127.0.0.1:8765/local/status)"; then ok=1; fi
        kill -0 "$pid" 2>/dev/null || break
    done
    local alive=0
    kill -0 "$pid" 2>/dev/null && alive=1
    kill -INT "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    echo "--- robotd log (last 25 lines)"
    tail -n 25 "$log"
    echo "--- /local/status (first 300 characters)"
    echo "${status:0:300}"
    local sim=0
    grep -q SIMULATED "$log" && sim=1
    echo "status answered: $ok, still running after 20 s: $alive, log says SIMULATED: $sim"
    [[ $ok -eq 1 && $alive -eq 1 && $sim -eq 1 ]]
}

visiond_video() {
    cd "$WORK/robot"
    .venv/bin/python scripts/synthetic_video_test.py
}

imports() {
    cd "$WORK/robot"
    .venv/bin/python -c "import mediapipe, cv2, numpy; print('mediapipe', mediapipe.__version__, 'cv2', cv2.__version__, 'numpy', numpy.__version__)"
}

# shellcheck source=/dev/null
echo "container: $(uname -m), $(. /etc/os-release && echo "$PRETTY_NAME") — emulated, NOT a Raspberry Pi"
phase "copy repository" copy_repo
phase "setup.sh --container-test" setup
if [[ -x "$WORK/robot/.venv/bin/python" ]]; then
    phase "robot tests (pytest)" robot_tests
    phase "robotd --sim 20 s" robotd_sim
    phase "visiond synthetic video" visiond_video
    phase "import mediapipe, cv2" imports
fi

echo
echo "================ container test summary (emulated arm64, $RELEASE; not a Raspberry Pi)"
for i in "${!NAMES[@]}"; do
    printf '%-28s %-8s %6s s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${TIMES[$i]}"
done
[[ -f /tmp/pytest.log ]] && echo "pytest: $(tail -n 1 /tmp/pytest.log)"
exit $OVERALL
