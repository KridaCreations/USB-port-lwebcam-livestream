#!/bin/bash

set -euo pipefail
# ============================================================
# Configuration
# ============================================================

IMAGE="/piusb.bin"
MOUNT_POINT="/mnt/tvusb"
VIDEO_NAME="live.ts"
DURATION=600

WIDTH=1280
HEIGHT=720
FPS=30


MUX_RATE=2000000
VIDEO_RATE=3300k
AUDIO_RATE=96k

FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
META_FILE="/home/abhishek/video_meta.env"


# ============================================================
# Cleanup existing gadget/NBD
# ============================================================

echo
echo "============================================================"
echo " PREPARING MPEG-TS TEST VIDEO"
echo "============================================================"
echo

echo "[1] Stopping USB mass-storage gadget..."
sudo modprobe -r g_mass_storage 2>/dev/null || true
sleep 1

echo "[2] Disconnecting NBD..."
sudo nbd-client -d /dev/nbd0 2>/dev/null || true
sleep 1

echo "[3] Stopping nbdkit..."
sudo pkill nbdkit 2>/dev/null || true
sleep 1

echo "[4] Removing NBD module..."
sudo modprobe -r nbd 2>/dev/null || true
sleep 1


# ============================================================
# Mount FAT32 image
# ============================================================

echo
echo "[5] Mounting $IMAGE..."

sudo mkdir -p "$MOUNT_POINT"

if mountpoint -q "$MOUNT_POINT"
then
    sudo umount "$MOUNT_POINT"
fi


sudo mount -o loop,rw,uid="$(id -u)",gid="$(id -g)",umask=0022 "$IMAGE" "$MOUNT_POINT"


#TARGET="$MOUNT_POINT/$VIDEO_NAME"
TARGET="/mnt/tvusb/live.ts"



# Remove previous video  ============================================================

echo
echo "[6] Removing previous $VIDEO_NAME..."

rm -f "$TARGET"
sync


# Generate video ============================================================

echo
echo "============================================================"
echo " GENERATING 10-MINUTE MPEG-TS"
echo "============================================================"
echo
echo "Resolution : ${WIDTH}x${HEIGHT}"
echo "FPS        : ${FPS}"
echo "Duration   : ${DURATION} seconds"
echo "Mux rate   : 4.0 Mbps"
echo


ffmpeg \
    -y \
    \
    -f lavfi \
    -i "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" \
    \
    -f lavfi \
    -i "anullsrc=r=48000:cl=stereo" \
    \
    -map 0:v:0 \
    -map 1:a:0 \
    \
    -vf "
        eq=brightness=-0.08:saturation=0.82,
        drawbox=
            x=0:
            y=0:
            w=iw:
            h=130:
            color=black@0.72:
            t=fill,
        drawtext=
            fontfile=${FONT}:
            text='MPEG-TS  USB READ ANALYSIS':
            fontsize=42:
            fontcolor=white:
            x=(w-text_w)/2:
            y=20,
        drawtext=
            fontfile=${FONT}:
            text='PLAYBACK TIME   %{pts\\:hms}':
            fontsize=30:
            fontcolor=white:
            x=(w-text_w)/2:
            y=76,
        drawbox=
            x=25:
            y=h-105:
            w=iw-50:
            h=80:
            color=black@0.68:
            t=fill,
        drawtext=
            fontfile=${FONT}:
            text='FRAME  %{n}':
            fontsize=27:
            fontcolor=white:
            x=48:
            y=h-89,
        drawtext=
            fontfile=${FONT}:
            text='1280x720  |  30 FPS  |  MPEG-TS  |  4.0 Mbps':
            fontsize=25:
            fontcolor=white:
            x=w-text_w-48:
            y=h-89
    " \
    \
    -c:v libx264 \
    -preset veryfast \
    -profile:v high \
    -level:v 4.0 \
    -pix_fmt yuv420p \
    \
    -r "$FPS" \
    -g 60 \
    -keyint_min 60 \
    -sc_threshold 0 \
    \
    -b:v "$VIDEO_RATE" \
    -minrate "$VIDEO_RATE" \
    -maxrate "$VIDEO_RATE" \
    -bufsize 6600k \
    \
    -x264-params \
    "nal-hrd=cbr:force-cfr=1" \
    \
    -c:a aac \
    -b:a "$AUDIO_RATE" \
    -ar 48000 \
    \
    -t "$DURATION" \
    \
    -metadata service_name="TV USB READ TEST" \
    -metadata service_provider="Raspberry Pi" \
    \
    -f mpegts \
    -muxrate "$MUX_RATE" \
    -mpegts_flags +resend_headers \
    \
    "$TARGET"


sync


echo
echo "============================================================"
echo " ANALYZING GENERATED VIDEO"
echo "============================================================"
echo


VIDEO_SIZE=$(stat -c "%s" "$TARGET")


VIDEO_DURATION=$(
    ffprobe \
        -v error \
        -show_entries format=duration \
        -of default=noprint_wrappers=1:nokey=1 \
        "$TARGET"
)


VIDEO_BITRATE=$(
    ffprobe \
        -v error \
        -show_entries format=bit_rate \
        -of default=noprint_wrappers=1:nokey=1 \
        "$TARGET"
)


echo "File size : $VIDEO_SIZE bytes"
echo "Duration  : $VIDEO_DURATION seconds"
echo "Bitrate   : $VIDEO_BITRATE bit/s"


# ============================================================
# Unmount before using mtools
# ============================================================

echo
echo "Unmounting image..."

sudo umount "$MOUNT_POINT"
sync


# ============================================================
# Get FAT cluster range
# ============================================================

echo
echo "Locating live.ts FAT clusters..."


CLUSTER_OUTPUT=$(
    mshowfat \
        -i "$IMAGE" \
        "::/$VIDEO_NAME"
)


echo "$CLUSTER_OUTPUT"


VIDEO_CLUSTERS=$(
    echo "$CLUSTER_OUTPUT" |
    sed -n 's/.*<\([0-9][0-9]*-[0-9][0-9]*\)>.*/\1/p'
)


if [ -z "$VIDEO_CLUSTERS" ]
then
    echo
    echo "ERROR: Unable to determine FAT cluster range."
    exit 1
fi


# ============================================================
# Save metadata for logger
# ============================================================

cat > "$META_FILE" <<EOF
VIDEO_NAME="$VIDEO_NAME"
VIDEO_CLUSTERS="$VIDEO_CLUSTERS"
VIDEO_SIZE="$VIDEO_SIZE"
VIDEO_BITRATE="$VIDEO_BITRATE"
VIDEO_DURATION="$VIDEO_DURATION"
EOF


echo
echo "============================================================"
echo " VIDEO READY"
echo "============================================================"
echo
echo "Video           : $VIDEO_NAME"
echo "FAT clusters    : $VIDEO_CLUSTERS"
echo "Size            : $VIDEO_SIZE bytes"
echo "Bitrate         : $VIDEO_BITRATE bit/s"
echo "Duration        : $VIDEO_DURATION seconds"
echo
echo "Metadata saved:"
echo "  $META_FILE"
echo


echo "Directory contents:"
echo

mdir -i "$IMAGE" ::

echo
echo "Cluster layout:"
echo

mshowfat -i "$IMAGE" "::/$VIDEO_NAME"

echo
echo "Done."
