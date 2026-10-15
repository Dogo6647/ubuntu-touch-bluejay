#!/bin/bash
# Makes a copy of boot.img that stops in the Halium initramfs shell: the
# installer image (out/boot-installer.img, from make-flashable.sh) and the
# debug image for reading the logs of a boot that didn't come up.
#
#   tools/make-debug-boot.sh BOOT_IMG OUTPUT_IMG [BREAK]
#
# BREAK is an initramfs-tools "break=" value; the default, "bottom", stops
# after the rootfs and userdata are mounted under /root. The initramfs then
# starts USB networking (CDC-NCM) and telnetd on 192.168.2.15:23.
#
# The copy keeps the input's header (version, OS version and patch level),
# kernel, ramdisk and command line, and adds "break=BREAK" to the command line.
#
# Needs python3 and git; fetches AOSP's mkbootimg and avbtool, at the commits
# below, into tools/.cache/.
set -e

MKBOOTIMG_URL=https://android.googlesource.com/platform/system/tools/mkbootimg
MKBOOTIMG_REV=d2bb0af5ba6d3198a3e99529c97eda1be0b5a093
AVB_URL=https://android.googlesource.com/platform/external/avb
AVB_REV=c5066a96caa7bf4150c0a8cc8cc14ab81733fdc7

fail() {
    echo "$(basename "$0"): $*" >&2
    exit 1
}

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
    echo "usage: $0 BOOT_IMG OUTPUT_IMG [BREAK]" >&2
    exit 2
fi
for tool in python3 git; do
    command -v "$tool" >/dev/null || fail "$tool is needed"
done
[ -f "$1" ] || fail "$1: no such file"
[ -d "$(dirname "$2")" ] || fail "$(dirname "$2"): no such directory"
IN=$(realpath "$1")
OUT=$(realpath "$(dirname "$2")")/$(basename "$2")
BREAK=${3:-bottom}
[ "$IN" != "$OUT" ] || fail "the output would overwrite $IN"
HERE="$(dirname "$(realpath "$0")")"
CACHE="$HERE/.cache"

# shellcheck source=../deviceinfo
source "$HERE/../deviceinfo"
[ -n "$deviceinfo_bootimg_partition_size" ] ||
    fail "deviceinfo has no deviceinfo_bootimg_partition_size"

# Reuses the checkout in tools/.cache/ when it is at the pinned commit
fetch() {
    local url=$1 rev=$2 dir="$CACHE/$3"
    if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$rev" ]; then
        return
    fi
    rm -rf "$dir"
    git init -q "$dir"
    git -C "$dir" fetch -q --depth 1 "$url" "$rev" || fail "can't fetch $rev from $url"
    git -C "$dir" checkout -q FETCH_HEAD
}
mkdir -p "$CACHE"
fetch "$MKBOOTIMG_URL" "$MKBOOTIMG_REV" mkbootimg
fetch "$AVB_URL" "$AVB_REV" avb

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# The tools import their own modules, so run them from their directories.
# unpack_bootimg prints the mkbootimg arguments that rebuild the image.
(cd "$CACHE/mkbootimg" &&
    python3 unpack_bootimg.py --boot_img "$IN" --out "$TMP/unpacked" \
        --format=mkbootimg -0 > "$TMP/args") ||
    fail "$IN: not a boot image mkbootimg can unpack"

ARGS=()
prev=
while IFS= read -r -d '' arg; do
    if [ "$prev" = --cmdline ]; then
        arg="${arg:+$arg }break=$BREAK"
    fi
    ARGS+=("$arg")
    prev=$arg
done < "$TMP/args"

(cd "$CACHE/mkbootimg" && python3 mkbootimg.py "${ARGS[@]}" --output "$OUT") ||
    fail "mkbootimg failed"
# avbtool picks a random salt by default; derive it from the image instead, so
# the same boot.img always gives the same output.
SALT=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$OUT")
(cd "$CACHE/avb" && python3 avbtool.py add_hash_footer --image "$OUT" \
    --partition_name boot --partition_size "$deviceinfo_bootimg_partition_size" \
    --salt "$SALT") ||
    fail "avbtool failed"

# Check the result
(cd "$CACHE/mkbootimg" &&
    python3 unpack_bootimg.py --boot_img "$OUT" --out "$TMP/check") > "$TMP/info" ||
    fail "$OUT: can't unpack the result"
grep -q "^command line args: .*break=$BREAK\$" "$TMP/info" ||
    fail "$OUT: break=$BREAK is not on the command line"
for part in kernel ramdisk; do
    cmp -s "$TMP/unpacked/$part" "$TMP/check/$part" ||
        fail "$OUT: the $part differs from the one in $IN"
done
echo "$OUT: stops at break=$BREAK"
