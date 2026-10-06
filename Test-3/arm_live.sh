#!/bin/bash

ARM_FILE="/run/tv_live_arm"
PLAYBACK_FLAG="/run/tv_playback_start"

if [ "$EUID" -ne 0 ]; then

    echo "Use sudo."
    exit 1
fi

rm -f "$PLAYBACK_FLAG"

echo "ARMED" > "$ARM_FILE"

echo
echo "========================================"
echo "LIVE MODE ARMED"
echo "========================================"
echo

echo "open live.ts on the TV."
echo
echo "Do NOT start the webcam manually."
echo
echo "The Pi will start it automatically when"
echo "the TV begins the real playback pass."
echo
