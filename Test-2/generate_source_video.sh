#!/bin/bash

set -euo pipefail

OUTPUT="/home/abhishek/live_source.ts"

DURATION=120
WIDTH=640
HEIGHT=360
FPS=25

# Simple low-bitrate MPEG-TS
MUX_RATE=2000000
VIDEO_RATE=1500k
AUDIO_RATE=64k

FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


echo
echo "=============================================="
echo " GENERATING SIMPLE 2-MINUTE MPEG-TS"
echo "=============================================="
echo

rm -f "$OUTPUT"


ffmpeg \
    -y \
    \
    -f lavfi \
    -i "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" \
    \
    -f lavfi \
    -i "anullsrc=r=44100:cl=stereo" \
    \
    -map 0:v:0 \
    -map 1:a:0 \
    \
    -vf "
        drawbox=x=0:y=0:w=iw:h=80:color=black@0.65:t=fill,
        drawtext=
            fontfile=${FONT}:
            text='USB READ TEST':
            fontsize=28:
            fontcolor=white:
            x=(w-text_w)/2:
            y=12,
        drawtext=
            fontfile=${FONT}:
            text='TIME %{pts\\:hms}':
            fontsize=22:
            fontcolor=white:
            x=(w-text_w)/2:
            y=47
    " \
    \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -pix_fmt yuv420p \
    \
    -r "$FPS" \
    -g 50 \
    -keyint_min 50 \
    -sc_threshold 0 \
    \
    -b:v "$VIDEO_RATE" \
    -minrate "$VIDEO_RATE" \
    -maxrate "$VIDEO_RATE" \
    -bufsize 3000k \
    \
    -c:a aac \
    -b:a "$AUDIO_RATE" \
    -ar 44100 \
    \
    -t "$DURATION" \
    \
    -f mpegts \
    -muxrate "$MUX_RATE" \
    -mpegts_flags +resend_headers \
    \
    "$OUTPUT"


sync


echo
echo "=============================================="
echo " VIDEO CREATED"
echo "=============================================="
echo


ffprobe \
    -v error \
    -show_entries format=duration,size,bit_rate \
    -of default=noprint_wrappers=1 \
    "$OUTPUT"


echo
echo "File:"
echo "  $OUTPUT"
echo
