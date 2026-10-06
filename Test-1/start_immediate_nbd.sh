#!/bin/bash

set -e


# ============================================================
# CONFIG
# ============================================================

IMAGE="/piusb.bin"
VIDEO="live.ts"
PROBE_MOUNT="/mnt/piusb_immediate_probe"
PLUGIN="/home/abhishek/tv_immediate_backend.py"
SOCKET="/run/tv_immediate_nbd.sock"
PID_FILE="/run/tv_immediate_nbdkit.pid"
NBD_DEVICE="/dev/nbd0"
WEBCAM_FILE="/dev/shm/tv_immediate_webcam.ts"
PLAYBACK_FLAG="/run/tv_immediate_start"
READ_LOG="/tmp/tv_immediate_reads.log"
NBDKIT_LOG="/tmp/tv_immediate_nbdkit.log"
BITRATE=2000000


# ============================================================
# ROOT
# ============================================================

if [ "$EUID" -ne 0 ]; then

    echo
    echo "Use:"
    echo
    echo "sudo ~/start_immediate_nbd.sh"
    echo

    exit 1

fi


echo
echo "============================================================"
echo " IMMEDIATE LIVE CAMERA / NBD EXPERIMENT"
echo "============================================================"
echo

echo "No TV-buffer drain will be performed."
echo


# ============================================================
# CHECK FILES
# ============================================================

if [ ! -f "$IMAGE" ]; then
    echo "ERROR: $IMAGE missing."
    exit 1

fi


if [ ! -f "$PLUGIN" ]; then
    echo "ERROR: $PLUGIN missing."
    exit 1

fi


# ============================================================
# REMOVE OLD USB GADGET
# ============================================================

echo "[1] Removing previous USB gadget..."


modprobe \
    -r \
    g_mass_storage \
    2>/dev/null \
    || true


# ============================================================
# NBD MODULE
# ============================================================

modprobe \
    nbd \
    max_part=8


# ============================================================
# DISCONNECT OLD NBD
# ============================================================

echo "[2] Disconnecting old NBD..."


nbd-client \
    -d \
    "$NBD_DEVICE" \
    2>/dev/null \
    || true


sleep 1


# ============================================================
# STOP PREVIOUS NBDKIT
# ============================================================

echo "[3] Stopping old nbdkit..."


if [ -f "$PID_FILE" ]; then

    OLD_PID=$(cat "$PID_FILE")

    kill "$OLD_PID" 2>/dev/null || true

    rm -f "$PID_FILE"

fi


pkill -f tv_immediate_backend.py 2>/dev/null || true


rm -f "$SOCKET" "$WEBCAM_FILE" "$PLAYBACK_FLAG" "$READ_LOG" "$NBDKIT_LOG"


# ============================================================
# DISCOVER live.ts EXTENT
# ============================================================

echo
echo "[4] Discovering live.ts..."


mkdir -p "$PROBE_MOUNT"


if mountpoint -q "$PROBE_MOUNT"; then
    umount "$PROBE_MOUNT"
fi


mount -o loop,ro "$IMAGE" "$PROBE_MOUNT"


LIVE_FILE="$PROBE_MOUNT/$VIDEO"


if [ ! -f "$LIVE_FILE" ]; then
    echo "ERROR: live.ts not found."
    umount "$PROBE_MOUNT"
    exit 1

fi


FILE_SIZE=$(
    stat -c%s "$LIVE_FILE"
)


FILEFRAG_OUTPUT=$(
    filefrag -v -e -b512 "$LIVE_FILE"
)


echo
echo "$FILEFRAG_OUTPUT"
echo


EXTENT_COUNT=$(
    echo "$FILEFRAG_OUTPUT" |
    grep -E \
        '^[[:space:]]*[0-9]+:' |
    wc -l
)


if [ "$EXTENT_COUNT" -ne 1 ]; then

    echo "ERROR: live.ts is not contiguous."

    umount "$PROBE_MOUNT"

    exit 1

fi


PHYSICAL_SECTOR=$(
    echo "$FILEFRAG_OUTPUT" |
    sed -nE \
    's/^[[:space:]]*0:[[:space:]]+[0-9]+\.\.[[:space:]]*[0-9]+:[[:space:]]*([0-9]+)\.\..*/\1/p' |
    head -n1
)


if [ -z "$PHYSICAL_SECTOR" ]; then

    echo "ERROR: physical sector not found."

    umount "$PROBE_MOUNT"

    exit 1

fi


LIVE_START=$(( PHYSICAL_SECTOR * 512 ))
LIVE_END=$(( LIVE_START + FILE_SIZE ))


echo "File size       : $FILE_SIZE"
echo "Physical sector : $PHYSICAL_SECTOR"
echo "LIVE_START      : $LIVE_START"
echo "LIVE_END        : $LIVE_END"


sync
umount "$PROBE_MOUNT"


# ============================================================
# START NBDKIT
# ============================================================

echo
echo "[5] Starting immediate-live backend..."


nbdkit \
    -f \
    -r \
    -U "$SOCKET" \
    python \
    "$PLUGIN" \
    file="$IMAGE" \
    live_start="$LIVE_START" \
    live_end="$LIVE_END" \
    webcam_file="$WEBCAM_FILE" \
    playback_flag="$PLAYBACK_FLAG" \
    bitrate="$BITRATE" \
    log_file="$READ_LOG" \
    > "$NBDKIT_LOG" 2>&1 &


NBDKIT_PID=$!


echo "$NBDKIT_PID" > "$PID_FILE"


echo "nbdkit PID: $NBDKIT_PID"


# ============================================================
# WAIT FOR SOCKET
# ============================================================

for i in $(seq 1 50); do
    if [ -S "$SOCKET" ]; then
        break
    fi
    if ! kill -0 "$NBDKIT_PID" 2>/dev/null; then
        echo
        echo "nbdkit failed:"
        echo
        cat "$NBDKIT_LOG"
        exit 1
    fi
    sleep 0.1
done


if [ ! -S "$SOCKET" ]; then
    echo "ERROR: socket not created."
    cat "$NBDKIT_LOG"
    exit 1

fi


# ============================================================
# CONNECT NBD
# ============================================================

echo "[6] Connecting $NBD_DEVICE..."
nbd-client -unix "$SOCKET" "$NBD_DEVICE" -readonly -block-size 512

sleep 1


# ============================================================
# VERIFY
# ============================================================

IMAGE_SIZE=$(stat -c%s "$IMAGE")
NBD_SIZE=$(blockdev --getsize64 "$NBD_DEVICE")


echo
echo "Image size : $IMAGE_SIZE"
echo "NBD size   : $NBD_SIZE"


if [ "$IMAGE_SIZE" -ne "$NBD_SIZE" ]; then
    echo "ERROR: size mismatch."
    exit 1
fi


# ============================================================
# USB GADGET
# ============================================================

echo
echo "[7] Starting USB mass-storage gadget..."


modprobe g_mass_storage file="$NBD_DEVICE" ro=1 removable=1 stall=0


sleep 1


echo
echo "============================================================"
echo " READY"
echo "============================================================"
echo

echo "Architecture:"
echo
echo "TV"
echo " ↓"
echo "g_mass_storage"
echo " ↓"
echo "/dev/nbd0"
echo " ↓"
echo "nbdkit"
echo " ↓"
echo "first normal playback pread"
echo " ↓"
echo "START CAMERA IMMEDIATELY"
echo
echo "NO buffer drain."
echo

echo "Start receiver:"
echo
echo "  python3 ~/webcam_immediate_receiver.py"
echo

echo "Start laptop sender:"
echo
echo "  python3 ~/webcam_immediate_sender.py <PI_IP> <CAMERA>"
echo

echo "Monitor:"
echo
echo "  tail -f $READ_LOG"
echo
