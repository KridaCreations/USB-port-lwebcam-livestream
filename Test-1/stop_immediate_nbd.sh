#!/bin/bash


PID_FILE="/run/tv_immediate_nbdkit.pid"
SOCKET="/run/tv_immediate_nbd.sock"
NBD_DEVICE="/dev/nbd0"
PLAYBACK_FLAG="/run/tv_immediate_start"
WEBCAM_FILE="/dev/shm/tv_immediate_webcam.ts"


if [ "$EUID" -ne 0 ]; then
    echo "Use sudo."
    exit 1
fi


echo
echo "Stopping immediate-live experiment..."
echo


echo "[1] Removing USB gadget..."
modprobe -r g_mass_storage 2>/dev/null || true
sleep 1


echo "[2] Disconnecting NBD..."
nbd-client -d "$NBD_DEVICE" 2>/dev/null || true
sleep 1
echo "[3] Stopping nbdkit..."

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    kill \"$PID" \2>/dev/null \|| true
    rm -f \"$PID_FILE"
fi

pkill -f tv_immediate_backend.py 2>/dev/null || true
rm -f "$SOCKET" "$PLAYBACK_FLAG" "$WEBCAM_FILE"


echo
echo "Stopped."
echo
