#!/usr/bin/env python3

import os
import socket
import subprocess
import sys
import time


PORT = 5000
CAMERA = "/dev/video2"
DEBUG_FILE = "/tmp/webcam_live_debug.ts"


def main():
    pi_ip = sys.argv[1]
    print(
        f"Connecting to {pi_ip}:{PORT}..."
    )

    sock = socket.create_connection((pi_ip,PORT))

    print("Connected.")
    print("Waiting for playback anchor...")

    # ========================================================
    # RECEIVE:
    # START <anchor_seconds> <mux_rate>
    # ========================================================

    command = b""

    while not command.endswith(b"\n"):
        data = sock.recv(1)
        if not data:
            raise RuntimeError("Pi disconnected before START")

        command += data

    text = command.decode().strip()
    parts = text.split()

    if (len(parts) != 3or parts[0] != "START"):
        raise RuntimeError(f"Unexpected command: {text}")

    anchor_seconds = float(parts[1])
    mux_rate = int(parts[2])

    # Preserve ratio from our known-working version:
    #
    # old:
    #   mux   = 1,000,000
    #   video =   700,000
    #
    # new:
    #   mux   = 2,000,000
    #   video = 1,400,000

    video_rate = int(mux_rate * 0.70)
    bytes_per_second = (mux_rate / 8.0)

    print()
    print(f"TV playback anchor = "f"{anchor_seconds:.3f}s")
    print(f"MPEG-TS mux rate = "f"{mux_rate:,} bit/s")
    print(f"H.264 video rate = "f"{video_rate:,} bit/s")
    print("Audio = disabled")
    print()

    # ========================================================
    # VIDEO FILTER
    # ========================================================

    vf = (
    "scale=640:360:"
    "force_original_aspect_ratio=decrease,"
    "pad=640:360:"
    "(ow-iw)/2:"
    "(oh-ih)/2,"
    "fps=25,"
    "setsar=1,"
    f"setpts=PTS-STARTPTS+{anchor_seconds}/TB"
	)

    # ========================================================
    # FFMPEG
    # ========================================================

    ffmpeg_command = [
    "ffmpeg",

    "-hide_banner",
    "-loglevel",
    "info",

    # Camera
    "-f",
    "v4l2",

    "-framerate",
    "30",

    "-video_size",
    "640x480",

    "-i",
    CAMERA,

    # Video processing
    "-vf",
    vf,

    "-an",

    # H.264
    "-c:v",
    "libx264",

    "-preset",
    "ultrafast",

    "-tune",
    "zerolatency",

    "-pix_fmt",
    "yuv420p",

    # Shorter GOP:
    # keyframe every 10 frames = 0.4 sec at 25fps
    "-g",
    "10",

    "-keyint_min",
    "10",

    "-sc_threshold",
    "0",

    "-bf",
    "0",

    "-b:v",
    str(video_rate),

    "-minrate",
    str(video_rate),

    "-maxrate",
    str(video_rate),

    "-bufsize",
    "700000",

    "-x264-params",
    "nal-hrd=cbr:force-cfr=1",

    # MPEG-TS
    "-muxrate",
    str(mux_rate),

    # Preserve the PTS we deliberately created with setpts.
    "-mpegts_copyts",
    "1",

    # Do not add MPEG-TS muxer startup delay.
    "-muxdelay",
    "0",

    "-muxpreload",
    "0",

    "-mpegts_flags",
    "+resend_headers",

    "-flush_packets",
    "1",

    "-f",
    "mpegts",

    "pipe:1"
	]

    print("=" * 70)
    print("STARTING WEBCAM MPEG-TS")
    print("=" * 70)

    print(
        f"mux       : "
        f"{mux_rate / 1_000_000:.3f} Mbps"
    )

    print(
        f"video     : "
        f"{video_rate / 1_000_000:.3f} Mbps"
    )

    print(
        "resolution: 640x360"
    )

    print(
        "fps       : 25"
    )

    print(
        f"debug copy: {DEBUG_FILE}"
    )

    print()

    try:
        os.remove(DEBUG_FILE)
    except FileNotFoundError:
        pass

    ffmpeg = subprocess.Popen(ffmpeg_command,stdout=subprocess.PIPE,stderr=None,bufsize=0)

    total_sent = 0
    start_time = time.monotonic()
    last_print = start_time

    with open(DEBUG_FILE,"wb",buffering=0) as debug_file:

        try:
            while True:
                data = ffmpeg.stdout.read(64 * 1024)

                if not data:
                    print()
                    print("FFmpeg stdout ended.")
                    break

                # Save exactly what we're sending.

                debug_file.write(data)

                try:
                    sock.sendall(data)

                except (BrokenPipeError,ConnectionResetError):
                    print()
                    print("Pi closed the TCP connection.")
                    break

                total_sent += len(data)
                now = time.monotonic()

                if (now - last_print>= 1.0):
                    produced_seconds = (total_sent / bytes_per_second)
                    wall = (now - start_time)

                    print(
                        "\r"
                        f"sent="
                        f"{total_sent / 1024 / 1024:7.2f} MiB"
                        f" | TS="
                        f"{produced_seconds:7.2f}s"
                        f" | wall="
                        f"{wall:7.2f}s"
                        f" | delta="
                        f"{produced_seconds-wall:+6.2f}s",
                        end="",
                        flush=True
                    )

                    last_print = now

        except KeyboardInterrupt:
            print()
            print("Stopping...")

        finally:
            print()
            ffmpeg.terminate()
            try:
                return_code = ffmpeg.wait(timeout=2)

            except subprocess.TimeoutExpired:
                ffmpeg.kill()
                return_code = ffmpeg.wait()

            print(
                f"FFmpeg exit code: "
                f"{return_code}"
            )

            print(
                f"Total sent: "
                f"{total_sent:,} bytes"
            )

            print(
                f"Debug stream: "
                f"{DEBUG_FILE}"
            )

            sock.close()


if __name__ == "__main__":
    main()
