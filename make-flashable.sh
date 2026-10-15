#!/bin/bash
# Turns the device tarball from ./build.sh into fastboot-flashable images:
# out/boot.img and out/system.img (Ubuntu Touch rootfs with the Halium 16
# system image inside). Needs Linux with sudo, e2fsprogs and img2simg.
#
# This is a development image: SSH and adbd accept logins without a password.
set -xe

HERE="$(dirname "$(realpath "$0")")"
WORK="$HERE/workdir"
OUT="$HERE/out"
OTA="$WORK/ota"

if [ ! -d "$WORK/stage" ] || [ ! -d "$WORK/build-tools" ]; then
    echo "$0: run ./build.sh first" >&2
    exit 1
fi

source "$HERE/deviceinfo"
cd "$WORK/stage"
"$WORK/build-tools/prepare-fake-ota.sh" "$OUT/device_${deviceinfo_codename}.tar.xz" "$OTA"
"$WORK/build-tools/system-image-from-ota.sh" "$OTA/ubuntu_command" "$OUT"
rm -f "$OUT/rootfs.img"
ls -la "$OUT"
