#!/bin/bash

set -euo pipefail


# ============================================================
# CONFIG
# ============================================================

OUTPUT="/home/abhishek/replacement.ts"

TARGET_SIZE=30004424
DURATION=120

WIDTH=640
HEIGHT=360
FPS=25

MUX_RATE=2000000
VIDEO_RATE=1400k

FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TEMP="/home/abhishek/replacement_temp.ts"


echo
echo "============================================================"
echo " CREATING HIGH-CONTRAST REPLACEMENT STREAM"
echo "============================================================"
echo

rm -f \
    "$TEMP" \
    "$OUTPUT"


# ============================================================
# GENERATE REPLACEMENT VIDEO
# ============================================================

ffmpeg \
    -y \
    -f lavfi \
    -i "color=c=black:size=${WIDTH}x${HEIGHT}:rate=${FPS}:duration=${DURATION}" \
    -vf "
        drawgrid=
            width=80:
            height=80:
            thickness=40:
            color=red@0.85,

        drawbox=
            x='mod(t*160\,w+120)-120':
            y=125:
            w=120:
            h=80:
            color=white:
            t=fill,

        drawbox=
            x='mod(t*160\,w+120)-115':
            y=130:
            w=110:
            h=70:
            color=black:
            t=fill,

        drawbox=
            x=0:
            y=0:
            w=iw:
            h=95:
            color=black@0.85:
            t=fill,

        drawtext=
            fontfile=${FONT}:
            text='REPLACEMENT STREAM':
            fontsize=42:
            fontcolor=yellow:
            borderw=3:
            bordercolor=black:
            x=(w-text_w)/2:
            y=12,

        drawtext=
            fontfile=${FONT}:
            text='SERVED BY NBDKIT':
            fontsize=25:
            fontcolor=white:
            borderw=2:
            bordercolor=black:
            x=(w-text_w)/2:
            y=62,

        drawbox=
            x=0:
            y=230:
            w=iw:
            h=130:
            color=black@0.70:
            t=fill,

        drawtext=
            fontfile=${FONT}:
            text='THIS IS NOT live.ts':
            fontsize=38:
            fontcolor=cyan:
            borderw=3:
            bordercolor=black:
            x=(w-text_w)/2:
            y=245,

        drawtext=
            fontfile=${FONT}:
            text='TIME %{pts\\:hms}':
            fontsize=32:
            fontcolor=white:
            borderw=2:
            bordercolor=black:
            x=(w-text_w)/2:
            y=300,

        drawbox=
            x=0:
            y=0:
            w=iw:
            h=ih:
            color=yellow@0.95:
            t='if(lt(mod(t\,1)\,0.5)\,10\,2)'
    " \
    -an \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -pix_fmt yuv420p \
    -r "$FPS" \
    -g 25 \
    -keyint_min 25 \
    -sc_threshold 0 \
    -bf 0 \
    -b:v "$VIDEO_RATE" \
    -minrate "$VIDEO_RATE" \
    -maxrate "$VIDEO_RATE" \
    -bufsize "$VIDEO_RATE" \
    -x264-params "nal-hrd=cbr:force-cfr=1" \
    -muxrate "$MUX_RATE" \
    -mpegts_flags +resend_headers \
    -flush_packets 1 \
    -f mpegts \
    "$TEMP"


# ============================================================
# FORCE EXACT BYTE SIZE
# ============================================================

python3 - "$TEMP" "$OUTPUT" "$TARGET_SIZE" <<'PY'
import os
import sys

src = sys.argv[1]
dst = sys.argv[2]
target = int(sys.argv[3])

TS = 188

if target % TS != 0:
    raise RuntimeError(
        f"Target size {target} is not divisible by 188"
    )

source_size = os.path.getsize(src)

print()
print("FFmpeg output :", source_size)
print("Target size   :", target)


# ============================================================
# COPY COMPLETE TS PACKETS ONLY
# ============================================================

copy_size = min(
    source_size,
    target
)

copy_size = (
    copy_size // TS
) * TS


with open(src, "rb") as source, \
     open(dst, "wb") as output:

    remaining = copy_size

    while remaining > 0:

        data = source.read(
            min(
                1024 * 1024,
                remaining
            )
        )

        if not data:
            raise RuntimeError(
                "Unexpected EOF"
            )

        output.write(data)

        remaining -= len(data)


# ============================================================
# MPEG-TS NULL PACKET
#
# PID 0x1FFF
# ============================================================

null_packet = bytes([
    0x47,
    0x1F,
    0xFF,
    0x10
]) + bytes(
    [0xFF] * 184
)


current_size = os.path.getsize(dst)


if current_size < target:

    missing = (
        target
        - current_size
    )

    if missing % TS != 0:

        raise RuntimeError(
            "Missing size is not TS aligned"
        )

    packets = (
        missing // TS
    )

    print(
        "Appending",
        packets,
        "null packets"
    )

    with open(
        dst,
        "ab"
    ) as output:

        for _ in range(packets):

            output.write(
                null_packet
            )


final_size = os.path.getsize(
    dst
)


print()
print("Final size :", final_size)
print("TS packets :", final_size // TS)
print("mod 188    :", final_size % TS)


if final_size != target:

    raise RuntimeError(
        f"Wrong size: "
        f"{final_size} != {target}"
    )


print()
print("SUCCESS")
PY


rm -f "$TEMP"

sync


echo
echo "============================================================"
echo " REPLACEMENT CREATED"
echo "============================================================"
echo


ffprobe \
    -v error \
    -show_entries \
    format=duration,size,bit_rate,start_time \
    -show_entries \
    stream=codec_name,width,height,r_frame_rate \
    -of default=noprint_wrappers=1 \
    "$OUTPUT"


echo
echo "File:"
echo "  $OUTPUT"
echo
