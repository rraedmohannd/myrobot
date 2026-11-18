# Backend image. One Dockerfile for both setups:
#
# * Laptop (docker-compose.yml): `build: ./backend`, default EXTRAS="dev,report" (pytest, ruff,
#   matplotlib). The compose file bind-mounts ./backend on /app and ./shared on /shared and runs
#   its own `uvicorn --reload` command, which the entrypoint simply executes.
# * Server (docker-compose.prod.yml, .github/workflows/image.yml): EXTRAS="" (no dev/test/report
#   packages) and the named build context `shared=../shared`, which bakes the robot <-> backend
#   protocol package (scr_protocol) into /shared. Runs as uid/gid 10001; the default command
#   `serve` migrates the database and starts uvicorn (docker-entrypoint.sh).

# Default for the `shared` context: an empty stage. A build that passes
# `--build-context shared=../shared` (or compose `additional_contexts`) replaces it.
FROM scratch AS shared

FROM python:3.12-slim

ARG EXTRAS="dev,report"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app:/shared

# Non-root user for production (the prod compose sets `user: "10001:10001"`).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Install dependencies first (cached layer), then the source code.
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN if [ -n "$EXTRAS" ]; then pip install ".[${EXTRAS}]"; else pip install .; fi

# Anonymous speaker voiceprints (docs/SPEAKERS.md): 3D-Speaker CAM++ VoxCeleb model, Apache-2.0, ~30 MB
ENV SPEAKER_MODEL_PATH=/models/speaker-campplus.onnx
RUN mkdir -p /models && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx', '/models/speaker-campplus.onnx')"

COPY --from=shared / /shared/
COPY . .

# Writable data folders (named volumes in production; a new volume takes this ownership and the
# files baked here, e.g. the golden/latency results of the repo, on its first use).
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN mkdir -p /app/data/media /app/data/tts_cache /app/data/golden/results /app/data/latency /backups \
    && chown -R 10001:10001 /app/data/media /app/data/tts_cache /app/data/golden/results /app/data/latency \
    && (python -c "import scr_protocol" 2>/dev/null \
        || echo "NOTE: scr_protocol is not in the image (no 'shared' build context): fine for the laptop, which bind-mounts ./shared; a server image needs --build-context shared=../shared")

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
