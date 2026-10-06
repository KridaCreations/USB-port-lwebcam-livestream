#!/bin/bash

set -e


# ============================================================
# CONFIG
# ============================================================

IMAGE="/piusb.bin"
VIDEO="live.ts"
REPLACEMENT="/home/abhishek/replacement.ts"
PROBE_MOUNT="/mnt/piusb_probe"
PLUGIN="/home/abhishek/tv_replacement_backend.py"
SOCKET="/run/tv_replacement_nbd.sock"
PID_FILE="/run/tv_replacement_nbdkit.pid"
NBD_DEVICE="/dev/nbd0"
READ_LOG="/tmp/tv_replacement_reads.log"
NBDKIT_LOG="/tmp/tv_replacement_nbdkit.log"


# ============================================================
# ROOT CHECK
# ============================================================

if [ "$EUID" -ne 0 ]; then
    echo
    echo "Run:"
    echo
    echo "sudo ~/start_replacement_nbd.sh"
    echo

    exit 1
fi


echo
echo "============================================================"
echo " NBDKIT replacement.ts EXPERIMENT"
echo "============================================================"
echo


# ============================================================
# CHECK FILES
# ============================================================

if [ ! -f "$IMAGE" ]; then
    echo "ERROR: $IMAGE not found."
    exit 1

fi


if [ ! -f "$REPLACEMENT" ]; then
    echo "ERROR: $REPLACEMENT not found."
    exit 1

fi


# ============================================================
# REMOVE OLD USB GADGET
# ============================================================

if lsmod |
   grep -q '^g_mass_storage'
then
    echo "[1] Removing previous USB gadget..."
    modprobe -r g_mass_storage 2>/dev/null || true

else
    echo "[1] No previous USB gadget."

fi


# ============================================================
# DISCONNECT OLD NBD
# ============================================================

echo "[2] Disconnecting previous NBD..."

nbd-client -d "$NBD_DEVICE" 2>/dev/null || true


# ============================================================
# STOP OLD NBDKIT
# ============================================================

echo "[3] Stopping previous nbdkit..."

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null || true
    rm -f "$PID_FILE"

fi


pkill -f tv_replacement_backend.py 2>/dev/null || true
rm -f "$SOCKET" "$READ_LOG" "$NBDKIT_LOG"


# ============================================================
# FIND live.ts PHYSICAL LOCATION
# ============================================================

echo
echo "[4] Discovering live.ts physical extent..."


mkdir -p "$PROBE_MOUNT"


if mountpoint -q "$PROBE_MOUNT"
then
    umount    "$PROBE_MOUNT"

fi


mount -o loop,ro "$IMAGE" "$PROBE_MOUNT"
LIVE_FILE="$PROBE_MOUNT/$VIDEO"


if [ ! -f "$LIVE_FILE" ]; then
    echo
    echo "ERROR:"
    echo "$LIVE_FILE not found."
    umount "$PROBE_MOUNT"
    exit 1

fi


FILE_SIZE=$(stat -c%s "$LIVE_FILE")
REPLACEMENT_SIZE=$(stat -c%s "$REPLACEMENT")


echo
echo "live.ts size      : $FILE_SIZE"
echo "replacement size : $REPLACEMENT_SIZE"


# ============================================================
# REQUIRE SAME SIZE
# ============================================================

if [ "$FILE_SIZE" -ne "$REPLACEMENT_SIZE" ]; then
    echo
    echo "ERROR:"
    echo "replacement.ts must have EXACTLY the same"
    echo "number of bytes as live.ts."
    echo

    umount "$PROBE_MOUNT"

    exit 1

fi


# ============================================================
# REQUIRE SINGLE EXTENT
# ============================================================

FILEFRAG_OUTPUT=$(filefrag -v -e -b512 "$LIVE_FILE")


echo
echo "$FILEFRAG_OUTPUT"
echo


EXTENT_COUNT=$(echo "$FILEFRAG_OUTPUT" | grep -E '^[[:space:]]*[0-9]+:' | wc -l)


if [ "$EXTENT_COUNT" -ne 1 ]; then
    echo
    echo "ERROR:"
    echo "This simple experiment expects live.ts"
    echo "to occupy one contiguous physical extent."
    echo
    echo "Extents found: $EXTENT_COUNT"
    umount "$PROBE_MOUNT"
    exit 1

fi


# ============================================================
# EXTRACT PHYSICAL START SECTOR
# ============================================================

PHYSICAL_SECTOR=$(
    echo "$FILEFRAG_OUTPUT" |
    sed -nE \
    's/^[[:space:]]*0:[[:space:]]+[0-9]+\.\.[[:space:]]*[0-9]+:[[:space:]]*([0-9]+)\.\..*/\1/p' |
    head -n1
)


if [ -z "$PHYSICAL_SECTOR" ]; then

    echo
    echo "ERROR:"
    echo "Could not determine physical sector."
    umount "$PROBE_MOUNT"

    exit 1

fi


LIVE_START=$(( PHYSICAL_SECTOR * 512 ))
LIVE_END=$(( LIVE_START + FILE_SIZE ))

echo "Physical sector : $PHYSICAL_SECTOR"
echo "LIVE_START      : $LIVE_START"
echo "LIVE_END        : $LIVE_END"
echo


# ============================================================
# OPTIONAL MEDIA CHECK
# ============================================================

echo "live.ts:"
ffprobe \
    -v error \
    -show_entries format=duration,size,bit_rate \
    -of default=noprint_wrappers=1 \
    "$LIVE_FILE" \
    || true


echo
echo "replacement.ts:"
ffprobe \
    -v error \
    -show_entries format=duration,size,bit_rate \
    -of default=noprint_wrappers=1 \
    "$REPLACEMENT" \
    || true


sync

umount "$PROBE_MOUNT"


# ============================================================
# LOAD NBD MODULE
# ============================================================

echo
echo "[5] Loading NBD module..."

modprobe nbd max_part=8


# ============================================================
# START NBDKIT
# ============================================================

echo "[6] Starting replacement backend..."


nbdkit \
    -f \
    -r \
    -U "$SOCKET" \
    python \
    "$PLUGIN" \
    file="$IMAGE" \
    replacement="$REPLACEMENT" \
    live_start="$LIVE_START" \
    live_end="$LIVE_END" \
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
    sleep 0.1
done


if [ ! -S "$SOCKET" ]; then
    echo
    echo "ERROR:"
    echo "nbdkit socket was not created."
    echo
    cat "$NBDKIT_LOG"

    exit 1

fi


# ============================================================
# CONNECT NBD
# ============================================================

echo "[7] Connecting /dev/nbd0..."


nbd-client \
    -unix "$SOCKET" \
    "$NBD_DEVICE" \
    -readonly \
    -block-size 512


sleep 1


IMAGE_SIZE=$(stat -c%s "$IMAGE")


NBD_SIZE=$(blockdev --getsize64 "$NBD_DEVICE")


echo
echo "Image size : $IMAGE_SIZE"
echo "NBD size   : $NBD_SIZE"


if [ "$IMAGE_SIZE" -ne "$NBD_SIZE" ]; then
    echo
    echo "ERROR: NBD size mismatch."
    exit 1

fi


# ============================================================
# EXPORT NBD AS USB MASS STORAGE
# ============================================================

echo
echo "[8] Starting USB mass-storage gadget..."


modprobe \
    g_mass_storage \
    file="$NBD_DEVICE" \
    ro=1 \
    removable=1 \
    stall=0


sleep 1


# ============================================================
# DONE
# ============================================================

echo
echo "============================================================"
echo " READY"
echo "============================================================"
echo

echo "TV sees:"
echo
echo "  /piusb.bin filesystem"
echo
echo "but reads of live.ts are served from:"
echo
echo "  $REPLACEMENT"

echo
echo "Original live.ts has NOT been modified."

echo
echo "Monitor:"
echo
echo "  tail -f $READ_LOG"
echo
