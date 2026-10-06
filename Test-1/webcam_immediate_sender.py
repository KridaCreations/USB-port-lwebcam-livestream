#!/usr/bin/env python3

import socket
import subprocess
import sys
import time


PORT = 5001

DEFAULT_CAMERA = "/dev/video0"


# ============================================================
# VIDEO
# ============================================================

CAPTURE_WIDTH = 640
CAPTURE_HEIGHT = 480
CAPTURE_FPS = 30

OUTPUT_WIDTH = 640
OUTPUT_HEIGHT = 360
OUTPUT_FPS = 25

VIDEO_BITRATE = "1400k"


# ============================================================
# START FFMPEG
# ============================================================

def start_ffmpeg(
    camera,
    anchor_seconds,
    mux_rate
):

    # Match the live stream PTS to the TV's logical
    # MPEG-TS position.

    vf = (
        f"scale="
        f"{OUTPUT_WIDTH}:"
        f"{OUTPUT_HEIGHT}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad="
        f"{OUTPUT_WIDTH}:"
        f"{OUTPUT_HEIGHT}:"
        f"(ow-iw)/2:"
        f"(oh-ih)/2,"
        f"fps={OUTPUT_FPS},"
        f"setsar=1,"
        f"setpts="
        f"PTS-STARTPTS+"
        f"{anchor_seconds}/TB"
    )


    command = [
        "ffmpeg",

        "-hide_banner",
        "-loglevel",
        "warning",

        "-f",
        "v4l2",

        "-thread_queue_size",
        "512",

        "-framerate",
        str(
            CAPTURE_FPS
        ),

        "-video_size",
        (
            f"{CAPTURE_WIDTH}"
            f"x"
            f"{CAPTURE_HEIGHT}"
        ),

        "-i",
        camera,

        "-vf",
        vf,

        "-an",

        "-c:v",
        "libx264",

        "-preset",
        "ultrafast",

        "-tune",
        "zerolatency",

        "-pix_fmt",
        "yuv420p",

        "-g",
        str(
            OUTPUT_FPS
        ),

        "-keyint_min",
        str(
            OUTPUT_FPS
        ),

        "-sc_threshold",
        "0",

        "-bf",
        "0",

        "-b:v",
        VIDEO_BITRATE,

        "-minrate",
        VIDEO_BITRATE,

        "-maxrate",
        VIDEO_BITRATE,

        "-bufsize",
        VIDEO_BITRATE,

        "-x264-params",
        "nal-hrd=cbr:force-cfr=1",

        "-muxrate",
        str(
            mux_rate
        ),

        "-mpegts_flags",
        "+resend_headers",

        "-flush_packets",
        "1",

        "-f",
        "mpegts",

        "pipe:1"
    ]


    print()
    print("=" * 70)
    print(" STARTING IMMEDIATE WEBCAM MPEG-TS")
    print("=" * 70)
    print()

    print(
        f"camera : {camera}"
    )

    print(
        f"anchor : {anchor_seconds:.6f}s"
    )

    print(
        f"mux    : {mux_rate / 1_000_000:.3f} Mbps"
    )

    print(
        f"video  : {VIDEO_BITRATE}"
    )

    print()


    return subprocess.Popen(
        command,

        stdout=subprocess.PIPE,

        bufsize=0
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if len(sys.argv) not in (
        2,
        3
    ):

        print(
            "Usage:"
        )

        print(
            "python3 webcam_immediate_sender.py "
            "<PI_IP> [CAMERA]"
        )

        sys.exit(1)


    pi_ip = (
        sys.argv[1]
    )


    if len(sys.argv) == 3:

        camera = (
            sys.argv[2]
        )

    else:

        camera = (
            DEFAULT_CAMERA
        )


    print(
        f"Connecting to "
        f"{pi_ip}:{PORT}..."
    )


    sock = socket.create_connection(
        (
            pi_ip,
            PORT
        )
    )


    sock.setsockopt(
        socket.IPPROTO_TCP,
        socket.TCP_NODELAY,
        1
    )


    print(
        "Connected."
    )

    print(
        "Camera is OFF."
    )

    print(
        "Waiting for first playback pread..."
    )


    # ========================================================
    # RECEIVE START COMMAND
    # ========================================================

    command = b""


    while not command.endswith(
        b"\n"
    ):

        data = sock.recv(
            1
        )


        if not data:

            raise RuntimeError(
                "Pi disconnected"
            )


        command += (
            data
        )


    text = (
        command.decode().strip()
    )


    parts = (
        text.split()
    )


    if (
        len(parts) != 4
        or parts[0] != "START"
    ):

        raise RuntimeError(
            f"Unexpected command: {text}"
        )


    anchor_seconds = float(
        parts[1]
    )

    mux_rate = int(
        parts[2]
    )

    max_bytes = int(
        parts[3]
    )


    print()
    print(
        "START received from Pi."
    )


    ffmpeg = start_ffmpeg(
        camera,
        anchor_seconds,
        mux_rate
    )


    sent = 0

    bytes_per_second = (
        mux_rate / 8.0
    )

    start_time = (
        time.monotonic()
    )

    last_print = 0.0


    try:

        while sent < max_bytes:

            data = ffmpeg.stdout.read(
                64 * 1024
            )


            if not data:

                print()
                print(
                    "FFmpeg stopped."
                )

                break


            remaining = (
                max_bytes
                - sent
            )


            if len(data) > remaining:

                data = data[
                    :remaining
                ]


            sock.sendall(
                data
            )


            sent += (
                len(data)
            )


            now = (
                time.monotonic()
            )


            if (
                now - last_print
                >= 0.5
            ):

                ts_seconds = (
                    sent
                    / bytes_per_second
                )


                wall = (
                    now
                    - start_time
                )


                print(
                    "\r"
                    f"sent="
                    f"{sent / 1024 / 1024:7.2f} MiB"
                    f" | TS="
                    f"{ts_seconds:7.2f}s"
                    f" | wall="
                    f"{wall:7.2f}s",
                    end="",
                    flush=True
                )


                last_print = (
                    now
                )


    except KeyboardInterrupt:

        print()
        print(
            "Stopping..."
        )


    finally:

        try:

            ffmpeg.terminate()

        except Exception:

            pass


        try:

            ffmpeg.wait(
                timeout=3
            )

        except Exception:

            ffmpeg.kill()


        sock.close()


if __name__ == "__main__":

    main()
