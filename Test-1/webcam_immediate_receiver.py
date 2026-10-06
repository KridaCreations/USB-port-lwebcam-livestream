#!/usr/bin/env python3

import os
import socket
import time


HOST = "0.0.0.0"
PORT = 5001


OUTPUT_FILE = ("/dev/shm/tv_immediate_webcam.ts")
PLAYBACK_FLAG = ("/run/tv_immediate_start")


def main():

    # ========================================================
    # CLEAN OLD SESSION
    # ========================================================

    try:
        os.remove(OUTPUT_FILE)

    except FileNotFoundError:
        pass


    try:
        os.remove(PLAYBACK_FLAG)

    except FileNotFoundError:
        pass


    # ========================================================
    # TCP SERVER
    # ========================================================

    server = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    server.bind((HOST,PORT))
    server.listen(1)


    print()
    print("=" * 70)
    print(" IMMEDIATE NBD LIVE CAMERA RECEIVER")
    print("=" * 70)
    print()

    print(f"Waiting for laptop on port {PORT}...")


    sock, address = (server.accept())
    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)


    print()
    print(
        f"Laptop connected: "
        f"{address[0]}:{address[1]}"
    )


    print()
    print(
        "Camera is still OFF."
    )

    print(
        "Waiting for first normal NBD pread "
        "after TV end probe..."
    )


    # ========================================================
    # WAIT FOR BACKEND START FLAG
    # ========================================================

    while not os.path.exists(PLAYBACK_FLAG):
        time.sleep(0.005)


    # ========================================================
    # READ START INFORMATION
    # ========================================================

    with open(PLAYBACK_FLAG,"r") as f:
        text = (f.read().strip())

    parts = (text.split())

    if len(parts) != 3:
        raise RuntimeError(f"Invalid playback flag: {text}")

    anchor_seconds = float(parts[0])
    mux_rate = int(parts[1])
    max_bytes = int(parts[2])
    bytes_per_second = (mux_rate / 8.0)


    print()
    print("=" * 70)
    print(" FIRST PLAYBACK pread DETECTED")
    print("=" * 70)
    print()

    print(
        f"TV anchor       : "
        f"{anchor_seconds:.6f}s"
    )

    print(
        f"MPEG-TS mux rate: "
        f"{mux_rate:,} bit/s"
    )

    print(
        f"Maximum bytes   : "
        f"{max_bytes:,}"
    )

    print()
    print(
        "NO BUFFER DRAIN."
    )

    print(
        "Starting laptop camera IMMEDIATELY."
    )


    # ========================================================
    # START LAPTOP CAMERA
    # ========================================================

    command = (
        f"START "
        f"{anchor_seconds:.9f} "
        f"{mux_rate} "
        f"{max_bytes}\n"
    )


    sock.sendall(command.encode())


    # ========================================================
    # CREATE RAM-BACKED STREAM
    # ========================================================

    fd = os.open(OUTPUT_FILE,os.O_WRONLY| os.O_CREAT| os.O_TRUNC,0o644)

    total = 0
    start_time = (time.monotonic())
    last_print = 0.0

    try:
        while total < max_bytes:
            data = sock.recv(64 * 1024)

            if not data:
                print()
                print("Laptop disconnected.")
                break


            remaining = (max_bytes - total)
            if len(data) > remaining:
                data = data[:remaining]

            os.write(fd,data)
            total += (len(data))
            now = (time.monotonic())

            if (now - last_print>= 0.25):
                produced = (total / bytes_per_second)
                wall = (now - start_time)

                print(
                    "\r"
                    f"produced={produced:7.2f}s"
                    f" | wall={wall:7.2f}s"
                    f" | difference="
                    f"{produced-wall:+6.2f}s",
                    end="",
                    flush=True
                )
                last_print = (now)


    except KeyboardInterrupt:
        print()
        print("Stopping receiver...")


    finally:
        print()
        os.close(fd)
        sock.close()
        server.close()
        print("Receiver stopped.")


if __name__ == "__main__":
    main()
