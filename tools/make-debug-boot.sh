#!/bin/bash
# Makes a debug copy of boot.img that stops in the Halium initramfs shell.
#
#   tools/make-debug-boot.sh boot.img boot-debug.img [break point]
#
# The break point is an initramfs-tools "break=" value; the default, "bottom",
# stops after the rootfs and userdata are mounted under /root. The initramfs
# then starts USB networking (CDC-NCM) and telnetd on 192.168.2.15:23.
#
# Needs python3 and git; fetches AOSP's mkbootimg and avbtool into
# tools/.cache/.
set -e

IN=$(realpath "$1")
OUT=$(realpath "$(dirname "$2")")/$(basename "$2")
BREAK=${3:-bottom}
HERE="$(dirname "$(realpath "$0")")"
CACHE="$HERE/.cache"

fetch() {
    [ -d "$CACHE/$2" ] || git clone -q --depth 1 "$1" "$CACHE/$2"
}
mkdir -p "$CACHE"
fetch https://android.googlesource.com/platform/system/tools/mkbootimg mkbootimg
fetch https://android.googlesource.com/platform/external/avb avb

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# The tools import their own modules, so run them from their directories
(cd "$CACHE/mkbootimg" && python3 unpack_bootimg.py --boot_img "$IN" --out "$TMP" >/dev/null)
(cd "$CACHE/mkbootimg" && python3 mkbootimg.py --header_version 4 \
    --kernel "$TMP/kernel" --ramdisk "$TMP/ramdisk" \
    --cmdline "break=$BREAK" --output "$OUT")
(cd "$CACHE/avb" && python3 avbtool.py add_hash_footer --image "$OUT" \
    --partition_name boot --partition_size 67108864)
echo "$OUT: stops at break=$BREAK"
