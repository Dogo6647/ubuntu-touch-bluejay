#!/bin/bash
# Turns the device tarball from ./build.sh into flashable images: out/boot.img
# and out/system.img (Ubuntu Touch rootfs with the Halium 16 system image
# inside). Needs Linux with sudo, e2fsprogs and img2simg.
#
# This is a development image: prepare-fake-ota.sh turns off adbd's host key
# check (ADBD_SECURE=0).
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

# prepare-fake-ota.sh doesn't stop on errors: after a failed download it still
# lists the tarball, and system-image-from-ota.sh then skips it.
for f in rootfs.tar.xz halium_halium_arm64.tar.xz "device_${deviceinfo_codename}.tar.xz" version.tar.xz; do
    if [ ! -s "$OTA/$f" ]; then
        echo "$0: $OTA/$f is missing" >&2
        exit 1
    fi
done
if [ "$(stat -c %s "$OTA/rootfs.tar.xz")" -lt 300000000 ]; then
    echo "$0: $OTA/rootfs.tar.xz is too small; the rootfs download failed" >&2
    exit 1
fi

"$WORK/build-tools/system-image-from-ota.sh" "$OTA/ubuntu_command" "$OUT"

for f in /usr/lib/aarch64-linux-gnu/libhybris/linker/q.so \
         /var/lib/lxc/android/android-rootfs.img \
         /usr/share/halium-overlay/vendor/bin/vndservicemanager; do
    if ! debugfs -R "stat $f" "$OUT/rootfs.img" 2>/dev/null | grep -q '^Inode:'; then
        echo "$0: $f is missing from the image" >&2
        exit 1
    fi
done
rm -f "$OUT/rootfs.img"
ls -la "$OUT"
