#!/bin/bash
# Builds the Ubuntu Touch adaptation for the Google Pixel 6a (bluejay).
#
#   ./build.sh        kernel, boot.img and the device tarball, into out/
#   ./build.sh -k     kernel and boot.img only
#   ./build.sh -s     only fetch the build tools and stage the port tree
#                     (enough for ./make-flashable.sh)
#
# The port is put together from these sources:
#   - UBports' halium-generic-adaptation-build-tools, which does the build
#   - UBports' generic GKI port (halium-gki): ramdisk and rootfs overlays
#     shared by GKI devices
#   - UBports' Pixel 9 port (google-zumapro), the first Halium 16 Tensor port:
#     its patched vndservicemanager
#   - libhybris from the UBports 26.04-1.x archive, which can load Android 16
#     libraries (24.04-2.x's libhybris can't yet)
#   - this repository: deviceinfo, the kernel config fragment and the
#     bluejay-specific overlays, applied on top of halium-gki's
set -xe

HERE="$(dirname "$(realpath "$0")")"

BUILD_TOOLS_URL=https://gitlab.com/ubports/porting/community-ports/halium-generic-adaptation-build-tools.git
BUILD_TOOLS_REV=7dd99e0de224931a7af1ffcc8d41f3c4c51f5316
HALIUM_GKI_URL=https://gitlab.com/ubports/porting/community-ports/android12/generic/halium-gki.git
HALIUM_GKI_REV=e05c1ebd0aab755747e6a0702ced130baeb42904
# The Halium kernel branch gets rebased; pin the commit whose ABI was checked
# against the stock BP4A.251205.006 vendor modules.
KERNEL_URL=https://gitlab.com/ubports/porting/community-ports/android12/generic/kernel-android-common.git
KERNEL_REV=994f2f422ca12fa39806bd0c405860117cce3252
ZUMAPRO_URL=https://gitlab.com/ubports/porting/community-ports/android16/google-pixel-9/google-zumapro.git
ZUMAPRO_REV=02787eaf5425c3b276effca2274a10ed83ad75b6

# libhybris with ELF TLS support (TLSDESC), which the Android 16 libraries use:
# https://gitlab.com/ubports/development/core/packaging/libhybris/-/merge_requests/65
# Drop this once 24.04-2.x ships it.
LIBHYBRIS_VER=0.1.0+git20260624+7079712-0ubports1+0~20260918102841.32+ubports26.04.1~1.gbpf11074
LIBHYBRIS_PKGS="libandroid-properties1 libhybris libhybris-common1 libhybris-test libhybris-utils libhardware2 libmedia1 libvulkan1-hybris"

WORK="$HERE/workdir"
STAGE="$WORK/stage"
OUT="$HERE/out"

fetch() {
    local url=$1 rev=$2 dir=$3
    [ -d "$dir/.git" ] || git init -q "$dir"
    git -C "$dir" fetch -q --depth 1 "$url" "$rev"
    git -C "$dir" checkout -q FETCH_HEAD
}

mkdir -p "$WORK" "$OUT"
fetch "$BUILD_TOOLS_URL" "$BUILD_TOOLS_REV" "$WORK/build-tools"
fetch "$HALIUM_GKI_URL" "$HALIUM_GKI_REV" "$WORK/halium-gki"
fetch "$ZUMAPRO_URL" "$ZUMAPRO_REV" "$WORK/zumapro"

rm -rf "$STAGE"
mkdir -p "$STAGE/overlay" "$STAGE/ramdisk-overlay"
cp "$HERE/deviceinfo" "$STAGE/deviceinfo"
for src in "$WORK/halium-gki" "$HERE"; do
    for dir in overlay ramdisk-overlay; do
        if [ -d "$src/$dir" ]; then
            cp -a "$src/$dir/." "$STAGE/$dir/"
        fi
    done
done

# The stock Android 16 vndservicemanager aborts without SELinux; use the
# Pixel 9 port's build with the Halium SELinux patch (its .rc is in overlay/).
mkdir -p "$STAGE/overlay/system/usr/share/halium-overlay/vendor/bin"
cp "$WORK/zumapro/overlay/system/usr/share/halium-overlay/vendor/bin/vndservicemanager" \
    "$STAGE/overlay/system/usr/share/halium-overlay/vendor/bin/vndservicemanager"

if [ "$1" = "-s" ]; then
    exit 0
fi

# build-tarball-mainline.sh copies <release>-overlay/system into the rootfs
# overlay, after overlay/.
source "$HERE/deviceinfo"
RELEASE_OVERLAY="$STAGE/${deviceinfo_ubuntu_touch_release}-overlay/system"
mkdir -p "$WORK/debs" "$RELEASE_OVERLAY"
for pkg in $LIBHYBRIS_PKGS; do
    deb="$WORK/debs/${pkg}_${LIBHYBRIS_VER}_arm64.deb"
    [ -s "$deb" ] || curl -fsSL -o "$deb" \
        "https://repo.ubports.com/pool/main/libh/libhybris/${pkg}_${LIBHYBRIS_VER}_arm64.deb"
    dpkg-deb -x "$deb" "$RELEASE_OVERLAY"
done

# Pre-clone the kernel at the pinned commit (the build tools skip their own
# clone when the directory exists) and add the bluejay config fragment.
KERNEL_DIR="$WORK/build/downloads/kernel-android-common"
mkdir -p "$WORK/build/downloads"
fetch "$KERNEL_URL" "$KERNEL_REV" "$KERNEL_DIR"
cp "$HERE/kernel/bluejay.config" "$KERNEL_DIR/arch/arm64/configs/bluejay.config"

cd "$STAGE"
"$WORK/build-tools/build.sh" -b "$WORK/build" -o "$OUT" "$@"
