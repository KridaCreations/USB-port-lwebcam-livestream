#!/usr/bin/env python3

import os
import time


API_VERSION = 2


# ============================================================
# CONFIG
# ============================================================

IMAGE_FILE = None
REPLACEMENT_FILE = None

LIVE_START = None
LIVE_END = None

LOG_FILE = "/tmp/tv_replacement_reads.log"

MUX_RATE = 2_000_000
BYTES_PER_SECOND = MUX_RATE / 8.0
SWITCH_SECONDS = 3.0
TS_PACKET_SIZE = 188


# ============================================================
# NBDKIT CONFIG
# ============================================================

def config(key, value):

    global IMAGE_FILE
    global REPLACEMENT_FILE
    global LIVE_START
    global LIVE_END
    global LOG_FILE

    global MUX_RATE
    global BYTES_PER_SECOND
    global SWITCH_SECONDS

    if key == "file":
        IMAGE_FILE = value

    elif key == "replacement":
        REPLACEMENT_FILE = value

    elif key == "live_start":
        LIVE_START = int(value)

    elif key == "live_end":
        LIVE_END = int(value)

    elif key == "log_file":
        LOG_FILE = value

    elif key == "mux_rate":
        MUX_RATE = int(value)
        BYTES_PER_SECOND = (MUX_RATE / 8.0)

    elif key == "switch_seconds":
        SWITCH_SECONDS = float(value)

    else:
        raise RuntimeError(
            f"Unknown parameter: {key}"
        )


# ============================================================
# CONFIG VALIDATION
# ============================================================

def config_complete():

    if IMAGE_FILE is None:
        raise RuntimeError("file= missing")

    if REPLACEMENT_FILE is None:
        raise RuntimeError("replacement= missing")

    if LIVE_START is None:
        raise RuntimeError("live_start= missing")

    if LIVE_END is None:
        raise RuntimeError("live_end= missing")

    if LIVE_END <= LIVE_START:
        raise RuntimeError("Invalid live.ts range")

    if not os.path.isfile(IMAGE_FILE):

        raise RuntimeError(
            f"Backing image does not exist: "
            f"{IMAGE_FILE}"
        )

    if not os.path.isfile(REPLACEMENT_FILE):
        raise RuntimeError(
            f"Replacement file does not exist: "
            f"{REPLACEMENT_FILE}"
        )


    live_size = (LIVE_END - LIVE_START)


    replacement_size = (os.path.getsize(REPLACEMENT_FILE))


    if replacement_size != live_size:

        raise RuntimeError(
            "\n"
            "replacement.ts size must exactly "
            "match live.ts.\n"
            f"live.ts     = {live_size}\n"
            f"replacement = {replacement_size}\n"
        )


    log_line(
        f"{time.monotonic():.6f} "
        f"mode=CONFIG "
        f"live_start={LIVE_START} "
        f"live_end={LIVE_END} "
        f"size={live_size} "
        f"mux_rate={MUX_RATE} "
        f"bytes_per_second="
        f"{BYTES_PER_SECOND:.3f} "
        f"switch_seconds="
        f"{SWITCH_SECONDS:.3f}"
    )


# ============================================================
# CONNECTION
# ============================================================

def open(readonly):
    image_fd = os.open(IMAGE_FILE,os.O_RDONLY | os.O_CLOEXEC)
    replacement_fd = os.open(REPLACEMENT_FILE,os.O_RDONLY| os.O_CLOEXEC)

    return {"image_fd": image_fd,"replacement_fd": replacement_fd}


def close(h):
    os.close(h["image_fd"])
    os.close(h["replacement_fd"])


def get_size(h):
    return os.fstat(h["image_fd"]).st_size


def can_write(h):
    return False


def can_multi_conn(h):
    return False


# ============================================================
# LOGGING
# ============================================================

def log_line(text):
    try:
        fd = os.open(
            LOG_FILE,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND
            | os.O_CLOEXEC,
            0o644
        )


        try:
            os.write(fd,(text + "\n").encode())


        finally:
            os.close(fd)


    except Exception:
        pass


# ============================================================
# READ EXACT NUMBER OF BYTES
# ============================================================

def exact_pread(fd,count,offset,description):
    data = os.pread(fd,count,offset)


    if len(data) != count:
        raise RuntimeError(
            f"Short {description} read: "
            f"{len(data)}/{count} "
            f"offset={offset}"
        )


    return data


# ============================================================
# CONVERT VIDEO BYTE POSITION TO SWITCH BLOCK
# ============================================================

def get_block_number(video_offset):
    video_seconds = (video_offset / BYTES_PER_SECOND)
    return int(video_seconds // SWITCH_SECONDS)


# ============================================================
# FIND PACKET-ALIGNED SWITCH BOUNDARY
#
# Important:
#
# 3 seconds at 2 Mbps:
#
# 3 * 250000 = 750000 bytes
#
# but:
#
# 750000 % 188 != 0
#
# so switching exactly there could splice two different
# transport streams inside one TS packet.
#
# We align the switch boundary DOWN to a whole 188-byte packet.
# ============================================================

def aligned_switch_byte(block_number):
    seconds = (block_number * SWITCH_SECONDS)
    raw_byte = int(seconds * BYTES_PER_SECOND)
    return (raw_byte // TS_PACKET_SIZE) * TS_PACKET_SIZE


# ============================================================
# MAIN READ
# ============================================================

def pread(h,buf,offset,flags):
    count = len(buf)
    request_end = (offset+ count)
    result = bytearray(exact_pread(h["image_fd"],count,offset,"backing image"))

    ## not live.ts  
    if ( request_end <= LIVE_START or offset >= LIVE_END):
        buf[:] = result
        return



    effective_start = max(offset,LIVE_START)
    effective_end = min(request_end,LIVE_END)
    current = (effective_start)


    # ========================================================
    # Process pieces independently.
    #
    # One 256 KiB NBD request can cross a 3-second switching
    # boundary, so we must split it internally.
    # ========================================================

    while current < effective_end:
        video_offset = (current - LIVE_START)
        block_number = (get_block_number(video_offset))

        # ----------------------------------------------------
        # Because boundaries are packet aligned, determine
        # next boundary explicitly.
        # ----------------------------------------------------

        next_block = (block_number + 1)
        next_boundary_video_byte = (aligned_switch_byte(next_block))


        # It is possible alignment-down places boundary behind
        # current position at an edge. Advance one block if so.

        while (next_boundary_video_byte <= video_offset):
            next_block += 1
            next_boundary_video_byte = (aligned_switch_byte(next_block))


        next_boundary_disk = (LIVE_START+ next_boundary_video_byte)
        segment_end = min(effective_end,next_boundary_disk)
        segment_count = (segment_end - current)


        if segment_count <= 0:
            break

        replacement_mode = ((block_number % 2) == 0)


        if replacement_mode:
            replacement_offset = (current - LIVE_START)

            replacement_data = (exact_pread(h["replacement_fd"],segment_count,replacement_offset,"replacement"))
            destination_start = (current - offset)
            destination_end = (destination_start + segment_count)


            result[destination_start:destination_end] = replacement_data
            mode_name = ("REPLACEMENT")


        else:


            # result already contains original bytes.
            #
            # Therefore nothing needs to be copied.

            mode_name = ("ORIGINAL")


        # ====================================================
        # LOG
        # ====================================================

        segment_video_start = ((current- LIVE_START)/ BYTES_PER_SECOND)
        segment_video_end = ((segment_end- LIVE_START)/ BYTES_PER_SECOND)

        log_line(
            f"{time.monotonic():.6f} "
            f"mode={mode_name} "
            f"block={block_number} "
            f"video="
            f"{segment_video_start:.3f}"
            f".."
            f"{segment_video_end:.3f} "
            f"bytes={segment_count} "
            f"disk="
            f"{current}"
            f".."
            f"{segment_end}"
        )


        current = (segment_end)


    # ========================================================
    # RETURN RESULT TO NBDKIT
    # ========================================================

    buf[:] = result
