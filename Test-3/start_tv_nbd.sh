#!/bin/bash

set -e


# ============================================================
# CONFIG
# ============================================================

IMAGE="/piusb.bin"
VIDEO="live.ts"

# New live.ts nominal MPEG-TS mux rate.
MUX_RATE=2000000

WEBCAM_FILE="/dev/shm/tv_webcam.ts"

ARM_FILE="/run/tv_live_arm"
PLAYBACK_FLAG="/run/tv_playback_start"

PROBE_MOUNT="/mnt/piusb_probe"

PLUGIN="/home/abhishek/tv_nbd_backend.py"

SOCKET="/run/tv_nbd.sock"
PID_FILE="/run/tv_nbdkit.pid"

NBD_DEVICE="/dev/nbd0"

BUCKET_FILE="/run/tv_nbd_bucket_seconds"
RATE_FILE="/run/tv_nbd_rate_bps"

READ_LOG="/tmp/tv_nbd_reads.log"
NBDKIT_LOG="/tmp/tv_nbdkit.log"


# ============================================================
# ROOT CHECK
# ============================================================

if [ "$EUID" -ne 0 ]; then

    echo
    echo "Run:"
    echo
    echo "sudo ~/start_tv_nbd.sh"
    echo

    exit 1
fi


echo
echo "============================================================"
echo " TV NBD 2 Mbps LIVE WEBCAM EXPERIMENT"
echo "============================================================"
echo


# ============================================================
# CLEAN OLD LOCAL MOUNTS
# ============================================================

if mountpoint -q \
    /mnt/usb_share \
    2>/dev/null
then

    echo "Unmounting /mnt/usb_share..."

    umount \
        /mnt/usb_share
fi


mkdir -p \
    "$PROBE_MOUNT"


if mountpoint -q \
    "$PROBE_MOUNT" \
    2>/dev/null
then

    umount \
        "$PROBE_MOUNT"
fi


# ============================================================
# MOUNT IMAGE READ-ONLY
# ============================================================

echo "Mounting image for live.ts discovery..."


mount \
    -o loop,ro \
    "$IMAGE" \
    "$PROBE_MOUNT"


if [ ! -f "$PROBE_MOUNT/$VIDEO" ]; then

    echo
    echo "ERROR:"
    echo "$VIDEO not found."

    umount \
        "$PROBE_MOUNT"

    exit 1
fi


FILE_SIZE=$(
    stat \
        -c%s \
        "$PROBE_MOUNT/$VIDEO"
)


OBSERVED_BITRATE=$(
    ffprobe \
        -v error \
        -show_entries format=bit_rate \
        -of default=noprint_wrappers=1:nokey=1 \
        "$PROBE_MOUNT/$VIDEO" \
        2>/dev/null \
        || true
)


OBSERVED_DURATION=$(
    ffprobe \
        -v error \
        -show_entries format=duration \
        -of default=noprint_wrappers=1:nokey=1 \
        "$PROBE_MOUNT/$VIDEO" \
        2>/dev/null \
        || true
)


echo
echo "live.ts:"
echo
echo "  size             = $FILE_SIZE bytes"
echo "  expected muxrate = $MUX_RATE bit/s"
echo "  ffprobe bitrate  = ${OBSERVED_BITRATE:-unknown}"
echo "  ffprobe duration = ${OBSERVED_DURATION:-unknown}"
echo


# ============================================================
# FIND CONTIGUOUS PHYSICAL EXTENT
# ============================================================

FILEFRAG_OUTPUT=$(
    filefrag \
        -v \
        -e \
        -b512 \
        "$PROBE_MOUNT/$VIDEO"
)


echo "$FILEFRAG_OUTPUT"


EXTENT_COUNT=$(
    echo "$FILEFRAG_OUTPUT" |
    grep -E \
        '^[[:space:]]*[0-9]+:' |
    wc -l
)


if [ "$EXTENT_COUNT" -ne 1 ]; then

    echo
    echo "ERROR:"
    echo "live.ts must be contiguous."
    echo
    echo "Extents found: $EXTENT_COUNT"

    umount \
        "$PROBE_MOUNT"

    exit 1
fi


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

    umount \
        "$PROBE_MOUNT"

    exit 1
fi


LIVE_START=$(( PHYSICAL_SECTOR * 512 ))

LIVE_END=$(( LIVE_START + FILE_SIZE ))


echo
echo "live.ts physical location:"
echo
echo "  sector = $PHYSICAL_SECTOR"
echo "  start  = $LIVE_START"
echo "  end    = $LIVE_END"
echo


sync

umount \
    "$PROBE_MOUNT"


echo "Probe mount removed."


# ============================================================
# REMOVE OLD USB GADGET
# ============================================================

if lsmod |
   grep -q '^g_mass_storage'
then

    echo
    echo "Removing old g_mass_storage..."

    modprobe \
        -r \
        g_mass_storage
fi


# ============================================================
# LOAD NBD
# ============================================================

echo
echo "Loading NBD module..."


modprobe \
    nbd \
    max_part=8


if nbd-client \
    -c "$NBD_DEVICE" \
    >/dev/null 2>&1
then

    echo "Disconnecting previous NBD..."

    nbd-client \
        -d \
        "$NBD_DEVICE" \
        2>/dev/null \
        || true

    sleep 1
fi


# ============================================================
# STOP PREVIOUS NBDKIT
# ============================================================

if [ -f "$PID_FILE" ]; then

    OLD_PID=$(
        cat "$PID_FILE"
    )

    if kill -0 \
        "$OLD_PID" \
        2>/dev/null
    then

        echo "Stopping previous nbdkit..."

        kill \
            "$OLD_PID"

        sleep 1
    fi

    rm -f \
        "$PID_FILE"
fi


rm -f \
    "$SOCKET" \
    "$READ_LOG" \
    "$NBDKIT_LOG"


# ============================================================
# RATE INFORMATION
# ============================================================

# 2,000,000 bits/sec / 8 = 250,000 bytes/sec.

echo "3.0" \
    > "$BUCKET_FILE"

echo "250000" \
    > "$RATE_FILE"


# ============================================================
# CLEAR LIVE STATE
# ============================================================

rm -f \
    "$ARM_FILE" \
    "$PLAYBACK_FLAG" \
    "$WEBCAM_FILE"


# ============================================================
# START NBDKIT
# ============================================================

echo
echo "Starting nbdkit Python backend..."


nbdkit \
    -f \
    -r \
    -U "$SOCKET" \
    python \
    "$PLUGIN" \
    file="$IMAGE" \
    live_start="$LIVE_START" \
    live_end="$LIVE_END" \
    bitrate="$MUX_RATE" \
    webcam_file="$WEBCAM_FILE" \
    arm_file="$ARM_FILE" \
    playback_flag="$PLAYBACK_FLAG" \
    log_file="$READ_LOG" \
    > "$NBDKIT_LOG" 2>&1 &


NBDKIT_PID=$!


echo "$NBDKIT_PID" \
    > "$PID_FILE"


echo \
    "nbdkit PID: $NBDKIT_PID"


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
    echo "NBD socket was not created."
    echo

    cat \
        "$NBDKIT_LOG"

    exit 1
fi


echo \
    "NBD server ready."


# ============================================================
# CONNECT NBD
# ============================================================

echo
echo "Connecting /dev/nbd0..."


nbd-client \
    -unix "$SOCKET" \
    "$NBD_DEVICE" \
    -readonly \
    -block-size 512


sleep 1


IMAGE_SIZE=$(
    stat \
        -c%s \
        "$IMAGE"
)


NBD_SIZE=$(
    blockdev \
        --getsize64 \
        "$NBD_DEVICE"
)


echo
echo "Original image size: $IMAGE_SIZE"
echo "NBD device size:     $NBD_SIZE"


if [ "$IMAGE_SIZE" -ne "$NBD_SIZE" ]; then

    echo
    echo "ERROR:"
    echo "NBD size mismatch."

    exit 1
fi


# ============================================================
# EXPORT TO TV
# ============================================================

echo
echo "Loading g_mass_storage using /dev/nbd0..."


modprobe \
    g_mass_storage \
    file="$NBD_DEVICE" \
    stall=0 \
    ro=1 \
    removable=1


sleep 1


# ============================================================
# STATUS
# ============================================================

echo
echo "============================================================"
echo " READY"
echo "============================================================"
echo

echo "TV backing path:"
cat \
    /sys/module/g_mass_storage/parameters/file

echo

echo "MPEG-TS mux rate:"
echo "$MUX_RATE bit/s"

echo

echo "MPEG-TS byte rate:"
echo "250000 bytes/s"

echo

echo "live.ts size:"
echo "$FILE_SIZE bytes"

echo

echo "Limiter/live state:"
echo "UNARMED"

echo

echo "Next:"
echo
echo "  1. Start webcam_live_receiver.py on Pi"
echo "  2. Start webcam_sender.py on laptop"
echo "  3. sudo ~/arm_live.sh"
echo "  4. Open live.ts on TV"
echo

echo "Monitor:"
echo
echo "tail -f $READ_LOG"
echo
