#!/usr/bin/env bash
# Choose the default speaker and microphone (PipeWire / WirePlumber on Raspberry Pi OS Bookworm).
# Run it inside the desktop session:  bash robot/deploy/audio-select.sh
# WirePlumber remembers the choice across reboots.
set -euo pipefail
echo "== Outputs (Sinks) and inputs (Sources) =="
wpctl status | sed -n '/Audio/,/Video/p'
echo
read -rp "Number of the SPEAKER (the I2S amplifier or the USB speaker), empty = keep: " sink
if [[ -n "$sink" ]]; then
    wpctl set-default "$sink"
    wpctl set-volume "$sink" 0.8
fi
read -rp "Number of the MICROPHONE (USB microphone or the webcam's), empty = keep: " source
if [[ -n "$source" ]]; then
    wpctl set-default "$source"
fi
echo "Test the speaker:  speaker-test -t wav -c 2 -l 1"
echo "Test the mic:      arecord -d 3 /tmp/mic.wav && aplay /tmp/mic.wav"
