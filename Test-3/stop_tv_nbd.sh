#!/bin/bash


echo
echo "Stopping USB mass storage..."

sudo modprobe -r g_mass_storage \
    2>/dev/null || true


sleep 1


echo "Disconnecting /dev/nbd0..."

sudo nbd-client -d /dev/nbd0 \
    2>/dev/null || true


sleep 1


echo "Stopping nbdkit..."

sudo pkill nbdkit \
    2>/dev/null || true


sleep 1


echo "Removing NBD module..."

sudo modprobe -r nbd \
    2>/dev/null || true


echo
echo "Done."
