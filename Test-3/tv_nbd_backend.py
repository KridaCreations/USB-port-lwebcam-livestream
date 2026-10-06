#!/usr/bin/env python3

import os
import threading
import time


API_VERSION = 2


# ============================================================
# CONFIG
# ============================================================

IMAGE_FILE = None

LIVE_START = None
LIVE_END = None

WEBCAM_FILE = "/dev/shm/tv_webcam.ts"
ARM_FILE = "/run/tv_live_arm"
PLAYBACK_FLAG = "/run/tv_playback_start"
LOG_FILE = "/tmp/tv_nbd_reads.log"


# ============================================================
# MPEG-TS
# ============================================================

# New live.ts:
#
# 2,000,000 bit/s
#
# 2,000,000 / 8
# = 250,000 bytes/s

VIDEO_BITRATE = 2_000_000.0
BYTES_PER_SECOND = (VIDEO_BITRATE / 8.0)


# MPEG-TS packets are always 188 bytes.
TS_PACKET_SIZE = 188


# Calculated during config_complete().
VIDEO_DURATION = None
END_PROBE_THRESHOLD = None


# The TV probes roughly the final part of live.ts before
# beginning its real playback pass.

END_PROBE_MARGIN_SECONDS = 10.0


# ============================================================
# LOW-LATENCY TV BUFFER DRAIN
# ============================================================

# Earlier experiments showed that leaving a small amount
# buffered is safer than trying to drain exactly to zero.
#
# The earlier successful automatic-drain build used ~0.5 s.

TARGET_REMAINING_BUFFER = 0.5


# Safety limit.
#
# The current 2-minute experiment should only need roughly
# 4-5 seconds of drain, but prevent a bad estimate from
# sleeping indefinitely.

MAX_DRAIN_SECONDS = 15.0


# Maximum wait for the webcam stream to generate requested
# live bytes.

MAX_WEBCAM_WAIT = 10.0


# ============================================================
# STATE
# ============================================================

state_lock = threading.Lock()


# Have we observed the TV's near-end duration probe?

saw_end_probe = False


# Have we completed buffer draining and started webcam mode?

playback_started = False


# Logical offset inside live.ts at which webcam byte 0 is
# inserted.
#
# This is always aligned to a 188-byte MPEG-TS packet.

playback_anchor_byte = None


# Same anchor expressed in nominal MPEG-TS seconds.

playback_anchor_seconds = None


# Timestamp of the LAST end-probe request.
#
# This is important because the TV performs several end reads.

last_end_probe_time = None


# ============================================================
# NBDKIT CONFIG
# ============================================================

def config(key, value):

    global IMAGE_FILE
    global LIVE_START
    global LIVE_END

    global WEBCAM_FILE
    global ARM_FILE
    global PLAYBACK_FLAG
    global LOG_FILE

    global VIDEO_BITRATE


    if key == "file":
        IMAGE_FILE = value


    elif key == "live_start":
        LIVE_START = int(value)


    elif key == "live_end":
        LIVE_END = int(value)


    elif key == "webcam_file":
        WEBCAM_FILE = value


    elif key == "arm_file":
        ARM_FILE = value


    elif key == "playback_flag":
        PLAYBACK_FLAG = value


    elif key == "log_file":
        LOG_FILE = value


    elif key == "bitrate":
        VIDEO_BITRATE = float(value)


    else:
        raise RuntimeError(f"Unknown parameter: {key}")


# ============================================================
# CONFIG VALIDATION
# ============================================================

def config_complete():

    global BYTES_PER_SECOND
    global VIDEO_DURATION
    global END_PROBE_THRESHOLD


    if IMAGE_FILE is None:
        raise RuntimeError("file= missing")


    if LIVE_START is None:
        raise RuntimeError("live_start= missing")


    if LIVE_END is None:
        raise RuntimeError("live_end= missing")


    if LIVE_END <= LIVE_START:
        raise RuntimeError("Invalid live.ts range")


    if not os.path.isfile(IMAGE_FILE):
        raise RuntimeError(f"{IMAGE_FILE} does not exist")


    if VIDEO_BITRATE <= 0:
        raise RuntimeError("Invalid MPEG-TS bitrate")


    BYTES_PER_SECOND = (VIDEO_BITRATE / 8.0)


    file_size = (LIVE_END - LIVE_START)


    VIDEO_DURATION = (file_size / BYTES_PER_SECOND)


    END_PROBE_THRESHOLD = max(0.0, VIDEO_DURATION - END_PROBE_MARGIN_SECONDS)


    log_line(
        f"{time.monotonic():.6f} "
        f"mode=CONFIG "
        f"bitrate={VIDEO_BITRATE:.0f} "
        f"bytes_per_second="
        f"{BYTES_PER_SECOND:.3f} "
        f"duration="
        f"{VIDEO_DURATION:.3f}s "
        f"end_probe_threshold="
        f"{END_PROBE_THRESHOLD:.3f}s "
        f"ts_packet={TS_PACKET_SIZE} "
        f"target_remaining="
        f"{TARGET_REMAINING_BUFFER:.3f}s"
    )


# ============================================================
# NBD CONNECTION
# ============================================================

def open(readonly):
    fd = os.open(IMAGE_FILE,os.O_RDONLY| os.O_CLOEXEC)
    return {"fd": fd}


def close(h):
    os.close(h["fd"])


def get_size(h):
    return os.fstat(h["fd"]).st_size


def can_write(h):
    return False


def can_multi_conn(h):
    return False


# ============================================================
# LOGGING
# ============================================================

def log_line(text):

    try:
        fd = os.open(LOG_FILE,os.O_WRONLY| os.O_CREAT| os.O_APPEND| os.O_CLOEXEC,0o644)

        try:
            os.write(fd,(text + "\n").encode())
        finally:
            os.close(fd)


    except Exception:
        # Logging must never break the USB read path.
        pass


# ============================================================
# ORIGINAL DISK READ
# ============================================================

def backing_read(h,count,offset):
    data = os.pread(h["fd"],count,offset)
    if len(data) != count:
        raise RuntimeError(f"Short backing read "f"{len(data)}/{count}")

    return data



# ============================================================
# MPEG-TS PACKET ALIGNMENT
# ============================================================

def align_ts_up(byte_offset):
    remainder = (byte_offset % TS_PACKET_SIZE)
    if remainder == 0:
        return byte_offset

    return (byte_offset+(TS_PACKET_SIZE - remainder))


# ============================================================
# START WEBCAM SIGNAL
# ============================================================

def signal_playback_start(anchor_seconds,max_source_bytes):

    # Receiver currently expects:
    # anchor mux_rate max_bytes
    # 6.012992000 2000000 28501176

    text = (
        f"{anchor_seconds:.9f} "
        f"{int(round(VIDEO_BITRATE))} "
        f"{int(max_source_bytes)}\n"
    )
    fd = os.open(PLAYBACK_FLAG,os.O_WRONLY| os.O_CREAT| os.O_TRUNC,0o644)

    try:
        os.write(fd,text.encode())


    finally:
        os.close(fd)


# ============================================================
# WEBCAM FILE SIZE / WAITING
# ============================================================

def webcam_size():

    try:

        return os.path.getsize(WEBCAM_FILE)


    except FileNotFoundError:
        return 0


def wait_for_webcam(required_end):

    start = (time.monotonic())


    while True:
        available = (webcam_size())
        if available >= required_end:
            return (True,available,time.monotonic()- start)

        elapsed = (time.monotonic() - start)
        if elapsed >= MAX_WEBCAM_WAIT:
            return (False,available,elapsed)

        time.sleep(0.003)


# ============================================================
# READ WEBCAM FILE
# ============================================================

def webcam_read(offset,count):
    fd = os.open(WEBCAM_FILE,os.O_RDONLY| os.O_CLOEXEC)
    try:
        data = os.pread(fd,count,offset)

    finally:
        os.close(fd)

    if len(data) != count:
        raise RuntimeError(f"Short webcam read "f"{len(data)}/{count}")


    return data



# MAIN NBD READ ============================================================

def pread(h,buf,offset,flags):
    global saw_end_probe
    global playback_started
    global playback_anchor_byte
    global playback_anchor_seconds
    global last_end_probe_time

    count = len(buf)
    request_end = (offset+ count)

    # ========================================================
    # REQUEST COMPLETELY OUTSIDE live.ts
    # ========================================================

    if ((request_end <= LIVE_START) or (offset >= LIVE_END)):
        buf[:] = backing_read(h,count,offset)
        return


    # ========================================================
    # START WITH ORIGINAL DISK CONTENT
    # ========================================================

    result = bytearray(backing_read(h,count,offset))
    effective_start = max(offset,LIVE_START)
    effective_end = min(request_end,LIVE_END)

    video_offset = (effective_start - LIVE_START)
    video_start = (video_offset/ BYTES_PER_SECOND)
    video_end = ((effective_end- LIVE_START)/ BYTES_PER_SECOND)


    # ========================================================
    # SYSTEM NOT ARMED
    # ========================================================

    if not os.path.exists(ARM_FILE):
        buf[:] = result
        log_line(
            f"{time.monotonic():.6f} "
            f"mode=IC "
            f"video={video_start:.3f}"
            f"..{video_end:.3f}"
        )


        return


    # ========================================================
    # COPY STATE
    # ========================================================

    with state_lock:
        local_started = playback_started
        local_saw_end = saw_end_probe


    # ========================================================
    # STARTUP / PROBE PHASE
    # ========================================================

    if not local_started:
        # ----------------------------------------------------
        # TV END/DURATION PROBE
        # ----------------------------------------------------

        if (video_start>= END_PROBE_THRESHOLD):
            now = (time.monotonic())
            with state_lock:
                saw_end_probe = True
                # Update for EVERY end-probe read.
                #
                # We need the final one.
                last_end_probe_time = (now)


            buf[:] = result
            log_line(
                f"{now:.6f} "
                f"mode=END_PROBE "
                f"video={video_start:.3f}"
                f"..{video_end:.3f}")
            return


        # ----------------------------------------------------
        # FIRST NORMAL READ AFTER END PROBE
        # ----------------------------------------------------

        if local_saw_end:
            first_post_probe_time = time.monotonic()
            raw_anchor_byte = video_offset

            # =================================================
            # ALIGN LIVE SPLICE TO MPEG-TS PACKET
            # =================================================
            aligned_anchor_byte = align_ts_up(raw_anchor_byte)

            alignment_skip = aligned_anchor_byte - raw_anchor_byte # for debugging only
            anchor_seconds = aligned_anchor_byte / BYTES_PER_SECOND


            # =================================================
            # ESTIMATE HOW MUCH VIDEO TV STILL HAS CACHED
            # =================================================
            with state_lock:
                probe_time = last_end_probe_time

            if probe_time is None:
                elapsed_since_probe = 0.0
            else:
                elapsed_since_probe = max(0.0,first_post_probe_time - probe_time)

            estimated_buffer = max(0.0,anchor_seconds - elapsed_since_probe)


            # =================================================
            # DRAIN ALMOST ALL OF THAT BUFFER
            # =================================================
            drain_seconds = (estimated_buffer- TARGET_REMAINING_BUFFER)
            drain_seconds = max(0.0,min(drain_seconds,MAX_DRAIN_SECONDS))


            log_line(
                f"{time.monotonic():.6f} "
                f"mode=AUTO_DRAIN "
                f"anchor="
                f"{anchor_seconds:.6f}s "
                f"elapsed_since_probe="
                f"{elapsed_since_probe:.3f}s "
                f"estimated_buffer="
                f"{estimated_buffer:.3f}s "
                f"target_remaining="
                f"{TARGET_REMAINING_BUFFER:.3f}s "
                f"drain="
                f"{drain_seconds:.3f}s "
                f"raw_anchor="
                f"{raw_anchor_byte} "
                f"aligned_anchor="
                f"{aligned_anchor_byte} "
                f"alignment_skip="
                f"{alignment_skip} "
                f"mod188="
                f"{aligned_anchor_byte % TS_PACKET_SIZE}"
            )


            # =================================================
            # CAMERA MUST STILL BE OFF HERE.
            #
            # This NBD read is deliberately blocked.
            #
            # The TV continues displaying/consuming video
            # already inside its own internal buffer.
            # =================================================

            drain_start = (
                time.monotonic())


            if drain_seconds > 0:
                time.sleep(drain_seconds)


            actual_drain = time.monotonic() - drain_start


            # =================================================
            # DRAIN FINISHED.
            #
            # NOW create webcam timeline zero at this anchor.
            # =================================================

            file_size = LIVE_END - LIVE_START
            remaining_bytes = file_size - aligned_anchor_byte

            # Remove any stale webcam stream.
            #
            # Do this AFTER draining so old live frames cannot
            # survive into the new session.

            try:
                os.remove(WEBCAM_FILE)

            except FileNotFoundError:
                pass

            with state_lock:
                playback_started = True
                playback_anchor_byte = (aligned_anchor_byte)
                playback_anchor_seconds = (anchor_seconds)

            # Tell receiver/laptop to start webcam NOW.
            signal_playback_start(anchor_seconds,remaining_bytes)
            log_line(
                f"{time.monotonic():.6f} "
                f"mode=LIVE_START "
                f"anchor="
                f"{anchor_seconds:.6f}s "
                f"auto_drain="
                f"{drain_seconds:.3f}s "
                f"actual_drain="
                f"{actual_drain:.3f}s "
                f"remaining="
                f"{remaining_bytes} "
                f"mod188="
                f"{aligned_anchor_byte % TS_PACKET_SIZE}"
            )


        else:
            # Still ordinary initial probing/cache filling.
            buf[:] = result
            log_line(
                f"{time.monotonic():.6f} "
                f"mode=PRE_SCAN "
                f"video={video_start:.3f}"
                f"..{video_end:.3f}"
            )


            return


    # ========================================================
    # LIVE PLAYBACK
    # ========================================================

    with state_lock:
        anchor_byte = (playback_anchor_byte)


    if anchor_byte is None:
        buf[:] = result
        return


    # Absolute disk offset of first webcam TS packet.
    splice_disk_offset = (LIVE_START + anchor_byte)


    # ========================================================
    # ONLY REPLACE BYTES AFTER THE ALIGNED SPLICE
    # ========================================================

    replace_start = max(effective_start,splice_disk_offset)
    replace_end = (effective_end)


    # Entire request occurs before webcam anchor.

    if replace_start >= replace_end:
        buf[:] = result
        log_line(
            f"{time.monotonic():.6f} "
            f"mode=BEFORE_ANCHOR "
            f"video={video_start:.3f}"
            f"..{video_end:.3f}"
        )

        return


    # ========================================================
    # TV POSITION -> WEBCAM POSITION
    #
    # Webcam byte 0 corresponds to aligned anchor.
    # ========================================================

    source_offset = (replace_start - splice_disk_offset)
    replace_bytes = (replace_end - replace_start)
    required_end = (source_offset + replace_bytes)


    # ========================================================
    # WAIT UNTIL EXACT WEBCAM BYTES EXIST
    # ========================================================
    (ready,available,wait_time) = wait_for_webcam(required_end)


    if not ready:
        buf[:] = result
        log_line(
            f"{time.monotonic():.6f} "
            f"mode=WEBCAM_TIMEOUT "
            f"source="
            f"{source_offset / BYTES_PER_SECOND:.3f}s "
            f"need="
            f"{required_end / BYTES_PER_SECOND:.3f}s "
            f"available="
            f"{available / BYTES_PER_SECOND:.3f}s "
            f"wait="
            f"{wait_time:.3f}s"
        )
        return


    # ========================================================
    # READ FRESH LIVE DATA
    # ========================================================
    live_data = webcam_read(source_offset,replace_bytes)


    # Position inside current NBD response.

    destination_start = (replace_start- offset)
    destination_end = (destination_start+ replace_bytes)

    result[destination_start:destination_end] = live_data
    buf[:] = result

    # ========================================================
    # DEBUG / LATENCY INFORMATION
    # ========================================================
    produced_seconds = (available/ BYTES_PER_SECOND)
    source_end_seconds = (required_end/ BYTES_PER_SECOND)
    backlog = (produced_seconds- source_end_seconds)


    tv_start = (
        (replace_start - LIVE_START)/ BYTES_PER_SECOND)

    tv_end = (
        (replace_end- LIVE_START) / BYTES_PER_SECOND)


    log_line(
        f"{time.monotonic():.6f} "
        f"mode=WEBCAM "
        f"tv="
        f"{tv_start:.3f}"
        f"..{tv_end:.3f} "
        f"source="
        f"{source_offset / BYTES_PER_SECOND:.3f}"
        f"..{source_end_seconds:.3f} "
        f"bytes="
        f"{replace_bytes} "
        f"produced="
        f"{produced_seconds:.3f}s "
        f"backlog="
        f"{backlog:.3f}s "
        f"wait="
        f"{wait_time:.3f}s"
    )
