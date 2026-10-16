#!/bin/bash
# Turns the device tarball from ./build.sh into flashable images, in out/:
#
#   boot.img            the Ubuntu Touch boot image
#   boot-installer.img  boot.img with "break=bottom", which stops in the Halium
#                       initramfs shell; tools/install.py copies the rootfs
#                       from there (made by tools/make-debug-boot.sh)
#   system.img          the rootfs (Ubuntu Touch with the Halium 16 system
#                       image inside), an Android sparse image; it becomes
#                       /userdata/ubuntu.img on the phone
#
#   ./make-flashable.sh [--variant devel|release]
#
# The variant's files, overlay-<variant>/, go in last, over the rootfs, the
# Halium GSI, the device tarball and the version tarball:
#
#   devel     (default) for porting and debugging: adb needs no authorization
#             (prepare-fake-ota.sh writes ADBD_SECURE=0), usb-moded's rescue
#             mode offers USB networking and SSH until lightdm starts, and the
#             journal keeps kernel messages
#   release   for users: adb asks to authorize each computer, no rescue mode
#
# Needs Linux with sudo, e2fsprogs, img2simg, lz4, python3 and git.
set -e

usage() {
    echo "usage: $0 [--variant devel|release]" >&2
    exit 2
}

VARIANT=devel
while [ $# -gt 0 ]; do
    case "$1" in
    --variant) [ $# -ge 2 ] || usage; VARIANT=$2; shift 2 ;;
    --variant=*) VARIANT=${1#*=}; shift ;;
    *) usage ;;
    esac
done
case "$VARIANT" in
devel|release) ;;
*) echo "$0: unknown variant '$VARIANT'" >&2; usage ;;
esac

set -x

HERE="$(dirname "$(realpath "$0")")"
WORK="$HERE/workdir"
OUT="$HERE/out"
OTA="$WORK/ota"

fail() {
    echo "$0: $*" >&2
    exit 1
}

if [ ! -d "$WORK/stage" ] || [ ! -d "$WORK/build-tools" ]; then
    fail "run ./build.sh first"
fi
if [ ! -d "$HERE/overlay-$VARIANT/system" ]; then
    fail "overlay-$VARIANT/system is missing"
fi

# shellcheck source=deviceinfo
source "$HERE/deviceinfo"
cd "$WORK/stage"
"$WORK/build-tools/prepare-fake-ota.sh" "$OUT/device_${deviceinfo_codename}.tar.xz" "$OTA"

# prepare-fake-ota.sh doesn't stop on errors: after a failed download it still
# lists the tarball, and system-image-from-ota.sh then skips it.
for f in rootfs.tar.xz halium_halium_arm64.tar.xz "device_${deviceinfo_codename}.tar.xz" version.tar.xz; do
    if [ ! -s "$OTA/$f" ]; then
        fail "$OTA/$f is missing"
    fi
done
if [ "$(stat -c %s "$OTA/rootfs.tar.xz")" -lt 300000000 ]; then
    fail "$OTA/rootfs.tar.xz is too small; the rootfs download failed"
fi

# What went into the image. The rootfs and the Halium GSI are UBports CI's
# latest successful builds, so record their checksums (and build numbers).
jenkins_build() {
    curl -fsSL "$1/lastSuccessfulBuild/buildNumber" 2>/dev/null || echo unknown
}
{
    echo "variant: $VARIANT"
    echo "port: $(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "ubuntu touch rootfs: ${deviceinfo_ubuntu_touch_release:-24.04-2.x}, build" \
        "$(jenkins_build "https://ci.ubports.com/job/ubuntu-touch-rootfs/job/ubports%252F${deviceinfo_ubuntu_touch_release:-24.04-2.x}")"
    echo "halium gsi: $deviceinfo_halium_version, build" \
        "$(jenkins_build "https://ci.ubports.com/job/UBportsCommunityPortsJenkinsCI/job/ubports%252Fporting%252Fcommunity-ports%252Fjenkins-ci%252Fgeneric_arm64/job/halium-16.0")"
    echo "sha256:"
    (cd "$OTA" && sha256sum ./*.tar.gz halium_halium_arm64.tar.xz "device_${deviceinfo_codename}.tar.xz")
} > "$OUT/build-info.txt"
cat "$OUT/build-info.txt"

# The variant tarball. Only files, no directory entries, so the image's
# directories keep their modes; normalized modes, so the checkout's umask
# doesn't matter.
#
# prepare-fake-ota.sh also writes the upstart jobs /etc/init/ssh.override (an
# sshd allowing empty passwords) and /etc/init/usb-tethering.conf. Nothing reads
# /etc/init under systemd (the rootfs has no upstart), and a tarball can't
# delete them (system-image-from-ota.sh applies a tarball's "removed" list only
# to images that don't start with "format system"), so overlay-release replaces
# them with comments.
(
    cd "$HERE/overlay-$VARIANT"
    find system ! -type d -print0 | LC_ALL=C sort -z |
        tar -cJf "$OTA/$VARIANT.tar.xz" --owner=root --group=root \
            --mode=a+rX,go-w --null -T -
)
touch "$OTA/$VARIANT.tar.xz.asc"

# Apply it last: just before the final "unmount system".
CMD="$OTA/ubuntu_command"
if [ "$(tail -n 1 "$CMD")" != "unmount system" ]; then
    fail "$CMD doesn't end with \"unmount system\""
fi
sed -i '$d' "$CMD"
printf 'update %s %s.asc\nunmount system\n' "$VARIANT.tar.xz" "$VARIANT.tar.xz" >> "$CMD"
cat "$CMD"

"$WORK/build-tools/system-image-from-ota.sh" "$CMD" "$OUT"

image_has() {
    debugfs -R "stat $1" "$OUT/rootfs.img" 2>/dev/null | grep -q '^Inode:'
}
image_cat() {
    debugfs -R "cat $1" "$OUT/rootfs.img" 2>/dev/null
}

for f in /usr/lib/aarch64-linux-gnu/libhybris/linker/q.so \
         /var/lib/lxc/android/android-rootfs.img \
         /usr/share/halium-overlay/vendor/bin/vndservicemanager; do
    image_has "$f" || fail "$f is missing from the image"
done

# Every variant file made it in unchanged, so nothing applied later replaced it
(cd "$HERE/overlay-$VARIANT" && find system ! -type d) | while read -r f; do
    image_cat "/${f#system/}" | cmp -s - "$HERE/overlay-$VARIANT/$f" ||
        fail "/${f#system/} in the image differs from overlay-$VARIANT/$f"
done

BRINGUP=/etc/systemd/journald.conf.d/zz-bluejay-bringup.conf
case "$VARIANT" in
devel)
    image_has "$BRINGUP" || fail "$BRINGUP is missing from the devel image"
    image_cat /etc/default/adbd | grep -qx 'ADBD_SECURE=0' ||
        fail "adbd asks for authorization in the devel image"
    ;;
release)
    image_cat /etc/default/adbd | grep -qx 'ADBD_SECURE=1' ||
        fail "adbd doesn't ask for authorization in the release image"
    image_cat /etc/default/usb-moded.d/zz-bluejay-release.conf |
        grep -qx 'USB_MODED_ARGS=""' ||
        fail "usb-moded's rescue mode is on in the release image"
    if image_has "$BRINGUP"; then
        fail "$BRINGUP is in the release image"
    fi
    ;;
esac
rm -f "$OUT/rootfs.img"

"$HERE/tools/make-debug-boot.sh" "$OUT/boot.img" "$OUT/boot-installer.img"

# Every file of ramdisk-overlay/ is in boot.img's ramdisk, unchanged. The
# ramdisk is lz4 over concatenated cpio archives; the later archive wins.
RD=$(mktemp -d)
(cd "$HERE/tools/.cache/mkbootimg" &&
    python3 unpack_bootimg.py --boot_img "$OUT/boot.img" --out "$RD" > /dev/null)
lz4 -dc "$RD/ramdisk" > "$RD/ramdisk.cpio"
python3 - "$RD/ramdisk.cpio" "$HERE/ramdisk-overlay" <<'EOF' || fail "boot.img's ramdisk lacks ramdisk-overlay/"
import os
import sys

data = open(sys.argv[1], "rb").read()
files = {}
off = 0
while off < len(data):
    if data[off] == 0:
        off += 1
        continue
    if data[off:off + 6] not in (b"070701", b"070702"):
        sys.exit(f"no cpio header at {off}")
    fields = [int(data[off + 6 + 8 * i:off + 14 + 8 * i], 16) for i in range(13)]
    size, namesize = fields[6], fields[11]
    name = data[off + 110:off + 109 + namesize].decode()
    start = (off + 110 + namesize + 3) & ~3
    files[os.path.normpath(name)] = data[start:start + size]
    off = (start + size + 3) & ~3
ok = True
for root, _dirs, names in os.walk(sys.argv[2]):
    for n in names:
        path = os.path.join(root, n)
        rel = os.path.relpath(path, sys.argv[2])
        if files.get(rel) != open(path, "rb").read():
            print(f"{rel}: missing or different in boot.img's ramdisk", file=sys.stderr)
            ok = False
sys.exit(0 if ok else 1)
EOF
rm -rf "$RD"
ls -la "$OUT"
