#!/bin/bash
# Builds pulseaudio-modules-droid-30 for Ubuntu Touch 24.04-2.x (PulseAudio
# 16.1) with upstream's fix for the startup crash on phones without a headphone
# jack (the Pixel 6a has none). The fix is upstream from 14.2.110, but UBports
# only ships that for 26.04 (PulseAudio 17), which can't load on 24.04-2.x.
#
#   packages/pulseaudio-modules-droid/build.sh OUTDIR
#
# Runs on arm64 Ubuntu 24.04 with the UBports 24.04-2.x archive configured
# (see .github/workflows/build.yml). Needs sudo for apt-get build-dep.
set -xe

HERE="$(dirname "$(realpath "$0")")"
mkdir -p "${1:?usage: $0 OUTDIR}"
OUT=$(realpath "$1")

# The upstream release and the UBports packaging of the module on the phone
# (14.2.109-0ubports2~...~4ad1b20+ubports24.04.2).
UPSTREAM_URL=https://github.com/mer-hybris/pulseaudio-modules-droid.git
UPSTREAM_REV=72273cc48b288847c45e6753998fb42672a3aeb3 # 14.2.109
PACKAGING_URL=https://gitlab.com/ubports/development/core/packaging/pulseaudio-modules-droid.git
PACKAGING_REV=4ad1b209e9c4231bbc34787575f494743644aa8f
VERSION=14.2.109-0ubports2+bluejay1

fetch() {
    local url=$1 rev=$2 dir=$3
    git init -q "$dir"
    git -C "$dir" fetch -q --depth 1 "$url" "$rev"
    git -C "$dir" checkout -q FETCH_HEAD
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fetch "$UPSTREAM_URL" "$UPSTREAM_REV" "$WORK/pulseaudio-modules-droid"
fetch "$PACKAGING_URL" "$PACKAGING_REV" "$WORK/packaging"
SRC="$WORK/pulseaudio-modules-droid"
rm -rf "$SRC/.git"
cp -a "$WORK/packaging/debian" "$SRC/"

for patch in "$HERE"/*.patch; do
    cp "$patch" "$SRC/debian/patches/"
    basename "$patch" >> "$SRC/debian/patches/series"
done

{
    echo "pulseaudio-modules-droid ($VERSION) noble; urgency=medium"
    echo
    echo "  * Pixel 6a port: apply upstream dfda983 (extevdev: fix startup crash"
    echo "    when no input device is found)."
    echo
    echo " -- Parya Rastegar <rastegar.parya3@gmail.com>  $(date -R)"
    echo
    cat "$SRC/debian/changelog"
} > "$WORK/changelog"
mv "$WORK/changelog" "$SRC/debian/changelog"

cd "$SRC"
sudo apt-get build-dep -y --allow-downgrades ./
dpkg-buildpackage -b -us -uc

# debian/rules puts the modules under lib/pulse-<pulseaudio version>/modules;
# make sure that matches the phone's PulseAudio.
deb=$(ls ../pulseaudio-modules-droid-30_"${VERSION}"_*.deb)
dpkg-deb -c "$deb" | grep -q '/usr/lib/pulse-16.1+dfsg1/modules/module-droid-card-30.so$'
cp "$deb" "$OUT/"
ls -la "$OUT"
