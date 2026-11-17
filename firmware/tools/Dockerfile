# Build/test image for the firmware's off-target environments (native, sim-check).
# The host needs only Docker: no compiler and no PlatformIO installation.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# 6.2.0: the pioarduino platform (Arduino-ESP32 core 3.x, env esp32-ci-pins-core3) needs PlatformIO >= 6.1.19.
RUN pip install --no-cache-dir platformio==6.2.0
# Pre-install the native platform so that test runs do not need the network for it.
RUN pio pkg install -g -p native
# Build output stays inside the container (fast; nothing is written into the mounted repo).
ENV PLATFORMIO_WORKSPACE_DIR=/root/.platformio/scr-workspace
WORKDIR /work/firmware
