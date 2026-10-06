#!/usr/bin/env python3

import os
import socket
import time


HOST = "0.0.0.0"
PORT = 5000

OUTPUT_FILE = "/dev/shm/tv_webcam.ts"
PLAYBACK_FLAG = "/run/tv_playback_start"


def main():

    try:
        os.remove(OUTPUT_FILE)

    except FileNotFoundError:
        pass

    try:
        os.remove(PLAYBACK_FLAG)

    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    server.bind((HOST,PORT))
    server.listen(1)

    print()
    print("=" * 70)
    print("2 Mbps LOW-LATENCY WEBCAM RECEIVER")
    print("=" * 70)
    print()
    print(f"Waiting for laptop on port {PORT}...")

    sock, address = server.accept()
    print()
    print(f"Laptop connected: "f"{address[0]}:{address[1]}")
    print()
    print("Laptop connected but FFmpeg ""has NOT started.")
    print()
    print("Waiting for TV playback anchor...")

    # ========================================================
    # WAIT FOR BACKEND TO DETECT REAL PLAYBACK
    # ========================================================

    while not os.path.exists(PLAYBACK_FLAG):
        time.sleep(0.01)

    # ========================================================
    # READ:
    #
    # anchor_seconds mux_rate max_bytes
    # ========================================================

    with open(PLAYBACK_FLAG,"r") as f:
        parts = (f.read().strip().split())

    if len(parts) != 3:
        raise RuntimeError("Invalid playback flag: "+ " ".join(parts))

    anchor_seconds = float(parts[0])
    mux_rate = int(parts[1])
    max_bytes = int(parts[2])
    bytes_per_second = (mux_rate / 8.0)

    print()
    print("=" * 70)
    print("LIVE PLAYBACK ANCHOR DETECTED")
    print("=" * 70)
    print()
    print(f"TV logical anchor : "f"{anchor_seconds:.3f} s")
    print(f"MPEG-TS mux rate  : "f"{mux_rate:,} bit/s")
    print(f"Byte rate         : "f"{bytes_per_second:,.3f} B/s")
    print(f"Maximum live data : "f"{max_bytes:,} bytes")
    print()
    print("Starting laptop webcam with ""matching timestamp and mux rate...")

    # ========================================================
    # TELL LAPTOP TO START
    # ========================================================

    command = (f"START "f"{anchor_seconds:.6f} "f"{mux_rate}\n")
    sock.sendall(command.encode())

    # ========================================================
    # CREATE WEBCAM TS BUFFER
    # ========================================================

    fd = os.open(OUTPUT_FILE,os.O_WRONLY| os.O_CREAT| os.O_TRUNC,0o644)
    total = 0
    start_time = time.monotonic()
    last_print = 0.0

    try:

        while total < max_bytes:
            data = sock.recv(64 * 1024)

            if not data:
                print()
                print("Laptop disconnected.")
                break

            remaining = (max_bytes- total)

            if len(data) > remaining:
                data = data[:remaining]

            os.write(fd,data)
            total += len(data)
            now = (time.monotonic())

            if (now - last_print>= 0.25):
                produced = (total / bytes_per_second)
                wall = (now - start_time)
                print(
                    "\r"
                    f"produced="
                    f"{produced:7.2f}s"
                    f" | wall="
                    f"{wall:7.2f}s"
                    f" | difference="
                    f"{produced-wall:+6.2f}s",
                    end="",
                    flush=True
                )

                last_print = now

    finally:
        print()
        os.close(fd)

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        sock.close()
        server.close()

        print("Receiver stopped.")

if __name__ == "__main__":
    main()
