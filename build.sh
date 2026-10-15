#!/bin/bash
# Builds the Ubuntu Touch adaptation for the Google Pixel 6a (bluejay).
#
#   ./build.sh        kernel, boot.img and the device tarball, into out/
#   ./build.sh -k     kernel and boot.img only
#   ./build.sh -s     only fetch the build tools and stage the port tree
#                     (enough for ./make-flashable.sh)
#
# The port is put together from three sources:
#   - UBports' halium-generic-adaptation-build-tools, which does the build
#   - UBports' generic GKI port (halium-gki): ramdisk and rootfs overlays
#     shared by GKI devices
#   - this repository: deviceinfo and the bluejay-specific overlays, applied
#     on top of halium-gki's
set -xe

HERE="$(dirname "$(realpath "$0")")"

BUILD_TOOLS_URL=https://gitlab.com/ubports/porting/community-ports/halium-generic-adaptation-build-tools.git
BUILD_TOOLS_REV=7dd99e0de224931a7af1ffcc8d41f3c4c51f5316
HALIUM_GKI_URL=https://gitlab.com/ubports/porting/community-ports/android12/generic/halium-gki.git
HALIUM_GKI_REV=e05c1ebd0aab755747e6a0702ced130baeb42904

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

if [ "$1" = "-s" ]; then
    exit 0
fi

cd "$STAGE"
"$WORK/build-tools/build.sh" -b "$WORK/build" -o "$OUT" "$@"
