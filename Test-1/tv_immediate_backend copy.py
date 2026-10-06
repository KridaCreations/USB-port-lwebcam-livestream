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

WEBCAM_FILE = "/dev/shm/tv_immediate_webcam.ts"
PLAYBACK_FLAG = "/run/tv_immediate_start"
LOG_FILE = "/tmp/tv_immediate_reads.log"


# Current live.ts:
#
# 2 Mbps
# = 250,000 bytes/sec

VIDEO_BITRATE = 2_000_000.0
BYTES_PER_SECOND = (VIDEO_BITRATE / 8.0)
TS_PACKET_SIZE = 188


# TV probes roughly the last portion of the file.

END_PROBE_MARGIN_SECONDS = 10.0
VIDEO_DURATION = None
END_PROBE_THRESHOLD = None


# Maximum time one NBD request may wait for webcam data.
MAX_WEBCAM_WAIT = 10.0


# STATE ============================================================

state_lock = threading.Lock()
saw_end_probe = False
playback_started = False
playback_anchor_byte = None
playback_anchor_seconds = None


# NBDKIT CONFIG ============================================================

def config(key, value):

    global IMAGE_FILE
    global LIVE_START
    global LIVE_END
    global WEBCAM_FILE
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


    BYTES_PER_SECOND = (VIDEO_BITRATE / 8.0)
    file_size = (LIVE_END - LIVE_START)
    VIDEO_DURATION = (file_size / BYTES_PER_SECOND)
    END_PROBE_THRESHOLD = max(0.0,VIDEO_DURATION - END_PROBE_MARGIN_SECONDS)


    log_line(
        f"{time.monotonic():.6f} "
        f"mode=CONFIG "
        f"bitrate={VIDEO_BITRATE:.0f} "
        f"bytes_per_second="
        f"{BYTES_PER_SECOND:.3f} "
        f"duration="
        f"{VIDEO_DURATION:.3f}s "
        f"end_probe_threshold="
        f"{END_PROBE_THRESHOLD:.3f}s"
    )


# ============================================================
# CONNECTION
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
# MPEG-TS ALIGNMENT
# ============================================================

def align_ts_up(byte_offset):
    remainder = (byte_offset % TS_PACKET_SIZE)
    if remainder == 0:
        return byte_offset

    return (byte_offset +(TS_PACKET_SIZE - remainder))


# ============================================================
# START WEBCAM SIGNAL
# ============================================================

def signal_playback_start(anchor_seconds,max_source_bytes):
    # File format:
    # anchor_seconds mux_rate max_bytes

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
# WEBCAM FILE HELPERS
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


def webcam_read(offset,count):
    fd = os.open(WEBCAM_FILE,os.O_RDONLY| os.O_CLOEXEC)

    try:
        data = os.pread(fd,count,offset)
    finally:
        os.close(fd)

    if len(data) != count:
        raise RuntimeError(f"Short webcam read "f"{len(data)}/{count}")

    return data


# ============================================================
# MAIN NBD READ
# ============================================================

def pread(h,buf,offset,flags):

    global saw_end_probe
    global playback_started
    global playback_anchor_byte
    global playback_anchor_seconds


    count = len(buf)
    request_end = (offset+ count)


    # REQUEST DOES NOT TOUCH live.ts ========================================================

    if (request_end <= LIVE_START or offset >= LIVE_END):
        buf[:] = backing_read(h,count,offset)
        return


    # ========================================================
    # START WITH ORIGINAL DISK BYTES
    # ========================================================

    result = bytearray(backing_read(h,count,offset))


    # ========================================================
    # FIND live.ts INTERSECTION
    # ========================================================

    effective_start = max(offset,LIVE_START)
    effective_end = min(request_end,LIVE_END)
    video_offset = (effective_start - LIVE_START)


    video_start = (video_offset / BYTES_PER_SECOND)
    video_end = ((effective_end - LIVE_START)/ BYTES_PER_SECOND)


    # ========================================================
    # COPY STATE
    # ========================================================

    with state_lock:
        local_started = (playback_started)
        local_saw_end = (saw_end_probe)

    # BEFORE LIVE PLAYBACK START ========================================================

    if not local_started:
        # ----------------------------------------------------
        # END-OF-FILE PROBE
        #
        # Do NOT serve webcam here.
        #
        # Otherwise the TV could request 115-120 seconds of
        # webcam before the camera has even produced it.
        # ----------------------------------------------------

        if (video_start>= END_PROBE_THRESHOLD):

            with state_lock:
                saw_end_probe = True


            buf[:] = result


            log_line(
                f"{time.monotonic():.6f} "
                f"mode=END_PROBE "
                f"video="
                f"{video_start:.3f}"
                f".."
                f"{video_end:.3f}"
            )


            return


        # ----------------------------------------------------
        # FIRST NORMAL pread AFTER END PROBE
        #
        # THIS IS THE KEY DIFFERENCE FROM THE FINAL VERSION:
        #
        # There is NO AUTO_DRAIN.
        #
        # Camera starts immediately here.
        # ----------------------------------------------------

        if local_saw_end:
            raw_anchor_byte = (video_offset)
            aligned_anchor_byte = (align_ts_up(raw_anchor_byte))
            anchor_seconds = (aligned_anchor_byte/ BYTES_PER_SECOND)
            alignment_skip = (aligned_anchor_byte- raw_anchor_byte)
            file_size = (LIVE_END- LIVE_START)
            remaining_bytes = (file_size- aligned_anchor_byte)


            # Remove previous camera session.

            try:
                os.remove(WEBCAM_FILE)

            except FileNotFoundError:
                pass


            try:
                os.remove(PLAYBACK_FLAG)

            except FileNotFoundError:
                pass


            # -----------------------------------------------
            # IMMEDIATELY switch state.
            #
            # NO buffer measurement.
            # NO sleep().
            # NO drain.
            # -----------------------------------------------

            with state_lock:
                playback_started = True
                playback_anchor_byte = (aligned_anchor_byte)
                playback_anchor_seconds = (anchor_seconds)


            signal_playback_start(anchor_seconds,remaining_bytes)


            log_line(
                f"{time.monotonic():.6f} "
                f"mode=IMMEDIATE_LIVE_START "
                f"raw_anchor="
                f"{raw_anchor_byte} "
                f"aligned_anchor="
                f"{aligned_anchor_byte} "
                f"alignment_skip="
                f"{alignment_skip} "
                f"mod188="
                f"{aligned_anchor_byte % 188} "
                f"anchor="
                f"{anchor_seconds:.6f}s "
                f"remaining="
                f"{remaining_bytes}"
            )


        else:

            # Initial scan before the TV probes EOF.

            buf[:] = result


            log_line(
                f"{time.monotonic():.6f} "
                f"mode=PRE_SCAN "
                f"video="
                f"{video_start:.3f}"
                f".."
                f"{video_end:.3f}"
            )


            return


    # ========================================================
    # LIVE WEBCAM MODE
    # ========================================================

    with state_lock:
        anchor_byte = (playback_anchor_byte)


    if anchor_byte is None:
        buf[:] = result
        return


    splice_disk_offset = (LIVE_START+ anchor_byte)


    # Preserve bytes before packet-aligned splice position
    # ========================================================
    replace_start = max(effective_start,splice_disk_offset)


    replace_end = (effective_end)


    if replace_start >= replace_end:
        buf[:] = result
        return


    # ========================================================
    # MAP:
    # TV anchor byte  -> webcam byte 0
    # anchor+X        -> webcam byte X
    # ========================================================

    source_offset = (replace_start- splice_disk_offset)
    replace_bytes = (replace_end- replace_start)
    required_end = (source_offset+ replace_bytes)


    # ========================================================
    # BLOCK THIS pread UNTIL CAMERA HAS PRODUCED ITS DATA
    # ========================================================

    (ready,available,wait_time) = wait_for_webcam(required_end)


    if not ready:
        # Failure-safe:
        # return original bytes instead of hanging the USB
        # device permanently.

        buf[:] = result
        log_line(
            f"{time.monotonic():.6f} "
            f"mode=WEBCAM_TIMEOUT "
            f"source="
            f"{source_offset / BYTES_PER_SECOND:.3f}"
            f".."
            f"{required_end / BYTES_PER_SECOND:.3f} "
            f"available="
            f"{available / BYTES_PER_SECOND:.3f}s "
            f"wait="
            f"{wait_time:.3f}s"
        )

        return


    # ========================================================
    # GET LIVE CAMERA BYTES
    # ========================================================

    live_data = webcam_read(source_offset,replace_bytes)
    destination_start = (replace_start - offset)
    destination_end = (destination_start + replace_bytes)
    result[destination_start:destination_end] = live_data


    buf[:] = result


    # LOG ========================================================
    produced_seconds = (available / BYTES_PER_SECOND)
    source_start_seconds = (source_offset / BYTES_PER_SECOND)
    source_end_seconds = (required_end / BYTES_PER_SECOND)
    backlog = (produced_seconds - source_end_seconds)


    log_line(
        f"{time.monotonic():.6f} "
        f"mode=WEBCAM "
        f"tv="
        f"{video_start:.3f}"
        f".."
        f"{video_end:.3f} "
        f"source="
        f"{source_start_seconds:.3f}"
        f".."
        f"{source_end_seconds:.3f} "
        f"bytes="
        f"{replace_bytes} "
        f"produced="
        f"{produced_seconds:.3f}s "
        f"backlog="
        f"{backlog:.3f}s "
        f"wait="
        f"{wait_time:.3f}s"
    )
