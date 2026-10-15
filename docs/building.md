# Building

This page explains how the images are built, in GitHub Actions and on your own
computer, how the devel and release variants differ, and how a release is made.

## Repository layout

| Path | What it is |
|---|---|
| `deviceinfo` | Device configuration for UBports' build tools: kernel, boot image, partitions |
| `build.sh` | Builds the kernel, `boot.img` and the device tarball; pins what it fetches itself |
| `make-flashable.sh` | Turns the device tarball into `boot.img`, `boot-installer.img` and `system.img` |
| `kernel/bluejay.config` | Kernel config fragment, merged after `halium.config` |
| `overlay/` | Files in every image, applied on top of halium-gki's overlay |
| `overlay-devel/` | Extra files only in devel images |
| `overlay-release/` | Extra files only in release images |
| `packages/` | Ubuntu Touch packages rebuilt with fixes for this port |
| `tools/install.py` | The installer |
| `tools/make-debug-boot.sh` | Makes a copy of `boot.img` that stops in the initramfs shell |
| `tests/` | Unit tests for `install.py` |
| `docs/` | This documentation |
| `.github/workflows/` | `build.yml`, `lint.yml` and `release.yml` |

## How the images are put together

1. **`build.sh`** fetches UBports' build tools, halium-gki and the Pixel 9
   port at pinned commits. It stages the port: halium-gki's `overlay/` and
   `ramdisk-overlay/`, then this repository's `overlay/` on top, plus the
   Pixel 9 port's patched `vndservicemanager`. It extracts the pinned
   libhybris packages from the 26.04-1.x archive and any package in
   `workdir/extra-debs/` into `24.04-2.x-overlay/`, the overlay for this
   Ubuntu Touch release, which the build tools apply after `overlay/`. It
   clones the kernel at the pinned commit, adds `kernel/bluejay.config`,
   downloads the Halium initramfs and checks its sha256. Then it runs the build
   tools, which build the kernel, `out/boot.img` and
   `out/device_bluejay.tar.xz`.
2. **`make-flashable.sh`** runs UBports' `prepare-fake-ota.sh`, which downloads
   the Ubuntu Touch rootfs and the Halium 16 GSI and writes an
   `ubuntu_command` file. The tarballs are applied in this order:
   1. `rootfs.tar.xz`: the Ubuntu Touch 24.04-2.x rootfs
   2. `halium_halium_arm64.tar.xz`: the Halium 16 GSI
   3. `device_bluejay.tar.xz`: the device tarball from `build.sh`
   4. `version.tar.xz`: the system-image channel settings
   5. `devel.tar.xz` or `release.tar.xz`: the files in `overlay-<variant>/`

   The variant tarball goes in last, so its files win. Before it packs that
   tarball, `make-flashable.sh` writes `out/build-info.txt` (see
   [below](#build-infotxt)). UBports' `system-image-from-ota.sh` then builds
   the ext4 rootfs image (`out/rootfs.img`) and its sparse copy
   (`out/system.img`). `make-flashable.sh` checks the ext4 image: key files
   are there, every variant file is in it unchanged, and the variant's
   settings are right (see below). It deletes `out/rootfs.img` and makes
   `boot-installer.img` from `boot.img` with `tools/make-debug-boot.sh`.

The outputs, in `out/`:

| File | What it is |
|---|---|
| `boot.img` | Boot image, header v4: the Halium GKI kernel and the Halium ramdisk |
| `boot-installer.img` | `boot.img` with `break=bottom` on the kernel command line; `install.py` copies the rootfs from its initramfs shell |
| `system.img` | The rootfs as an Android sparse image (5 GiB expanded); becomes `/userdata/ubuntu.img` |
| `build-info.txt` | What went into the images (below) |

### build-info.txt

`make-flashable.sh` records what went into the images:

- `variant`: `devel` or `release`
- `port`: the commit of this repository (`git rev-parse HEAD`; uncommitted
  changes aren't recorded)
- `ubuntu touch rootfs` (24.04-2.x) and `halium gsi` (16), each with its
  UBports CI build number
- `sha256`: the SHA-256 of the downloaded rootfs archive (`*.tar.gz`), of
  `halium_halium_arm64.tar.xz` and of `device_bluejay.tar.xz`

The rootfs and the GSI aren't pinned (see [Pinned versions](#pinned-versions)),
so this file tells two builds apart. The build numbers are those of UBports
CI's last successful builds, read just after the download, and `unknown` when
CI didn't answer; the SHA-256 lines are exact. The `flashable-bluejay`
artifact and every release include the file, and `SHA256SUMS` lists it.

## Image variants

```sh
./make-flashable.sh [--variant devel|release]
```

| | devel (default) | release |
|---|---|---|
| Used for | porting and debugging; CI builds of pushes and pull requests | releases (`release.yml`) |
| adb | No authorization (`ADBD_SECURE=0`, written by `prepare-fake-ota.sh`) | Asks on the phone (`/etc/default/adbd`: `ADBD_SECURE=1`) |
| usb-moded rescue mode: USB networking and SSH, which accepts empty passwords, until lightdm starts | On (usb-moded's default `-r`) | Off (`/etc/default/usb-moded.d/zz-bluejay-release.conf`: `USB_MODED_ARGS=""`) |
| Kernel messages in the journal | Yes (`journald.conf.d/zz-bluejay-bringup.conf`: `ReadKMsg=yes`) | No |
| Upstart jobs `/etc/init/ssh.override` and `/etc/init/usb-tethering.conf` | As `prepare-fake-ota.sh` writes them | Comment-only files (`overlay-release/system/etc/init/`) |

After the build, `make-flashable.sh` checks these settings in the image and
fails if one is wrong.

`prepare-fake-ota.sh` writes the two upstart jobs for development:
`ssh.override` starts an sshd that allows empty passwords. Nothing reads
`/etc/init` under systemd, so they have no effect. A tarball can't delete files
from an image that starts with `format system`, so release images replace both
with files that hold only a comment.

To put a file in only one variant, add it under `overlay-devel/system/` or
`overlay-release/system/` with its path in the image. Only files are packed,
owned by root with normalized modes, so the image's directories keep their
own modes.

## CI

Three workflows run in GitHub Actions:

- **`build.yml`** runs on pushes to `main` (except changes only to
  `README.md`, `CHANGELOG.md` or `docs/`), on pull requests, by hand, and from
  `release.yml`. It builds the devel variant unless it's asked for the
  release variant. It takes about 25 minutes. Jobs:
  1. `packages` (arm64 runner): builds the packages in `packages/` against
     the UBports 24.04-2.x archive.
  2. `build`: `./build.sh`, with the packages from the first job in
     `workdir/extra-debs/`.
  3. `flashable`: `./build.sh -s` and `make-flashable.sh` for the variant,
     then compresses `system.img` with zstd and writes `SHA256SUMS` over
     `boot.img`, `boot-installer.img`, `system.img.zst` and `build-info.txt`.

  Artifacts:

  | Artifact | Files |
  |---|---|
  | `extra-debs` | the rebuilt packages (`*.deb`) |
  | `device-bluejay` | `device_bluejay.tar.xz`, `device_bluejay.tar.build`, `Module.symvers` |
  | `flashable-bluejay` | `boot.img`, `boot-installer.img`, `system.img.zst`, `build-info.txt`, `SHA256SUMS` |

- **`lint.yml`** runs on pushes to `main`, on pull requests and from
  `release.yml`. It runs shellcheck on every tracked shell script, actionlint
  on the workflows, compiles the Python files, and runs the unit tests, also
  under Python 3.8, the oldest version `install.py` supports.
- **`release.yml`** runs when a `v*` tag is pushed. See
  [Making a release](#making-a-release).

To build the release variant without making a release:

```sh
gh workflow run build.yml -f variant=release
```

To install a CI build, download its `flashable-bluejay` artifact and point the
installer at it:

```sh
gh run download <run-id> -n flashable-bluejay -D build
python3 tools/install.py --images build
```

## Building locally

The build runs on Linux x86_64. CI uses Ubuntu 24.04. Install the dependencies
of both CI jobs:

```sh
sudo apt-get install --no-install-recommends \
    bc bison build-essential ca-certificates cpio curl fakeroot flex git kmod \
    libbpf-dev libelf-dev libssl-dev lz4 pahole python-is-python3 python3 \
    unzip wget xz-utils android-sdk-libsparse-utils e2fsprogs jq zstd
```

Then:

```sh
./build.sh                            # kernel, boot.img and device tarball, into out/
./make-flashable.sh                   # devel images, into out/ (needs sudo)
./make-flashable.sh --variant release # release images
```

`./build.sh -k` builds only the kernel and `boot.img`. `./build.sh -s` only
fetches the build tools and stages the port, which is enough for
`make-flashable.sh` when `out/device_bluejay.tar.xz` comes from elsewhere (CI
does this). `build.sh` fetches into `workdir/`, `tools/make-debug-boot.sh`
caches mkbootimg and avbtool in `tools/.cache/`, and outputs go into `out/`.
Git ignores all three, and the `__pycache__/` directories that the tests leave
behind.

**Rebuilt packages.** `build.sh` adds every `.deb` in `workdir/extra-debs/` to
the image. Without them the image has UBports' own pulseaudio-modules-droid,
which crashes on this phone, so there is no sound. Download them from a CI run:

```sh
gh run download <run-id> -n extra-debs -D workdir/extra-debs
```

or build them yourself on arm64 (see [packages](#packages)).

To install a local build, `install.py` takes `system.img` as well as
`system.img.zst`:

```sh
python3 tools/install.py --images out
```

Without a `SHA256SUMS` file in `out/`, the installer warns that it can't check
the files.

### Tests and lint

```sh
python3 -m unittest discover -s tests -v
```

The tests don't talk to a phone or the network. The zstd tests are skipped when
`zstd` isn't installed.

For shell scripts and workflows, run what `lint.yml` runs:

```sh
shellcheck -x -P SCRIPTDIR build.sh make-flashable.sh tools/make-debug-boot.sh \
    packages/*/build.sh overlay/system/var/lib/lxc/android/mount.sh \
    overlay/system/usr/libexec/lxc-android-config/device-hacks \
    overlay/system/usr/libexec/bluejay/audio-policy overlay/system/etc/profile.d/*.sh
actionlint
```

`lint.yml` checks every tracked `*.sh` file and every file with an `sh`,
`bash` or `dash` shebang, including the scripts in `overlay/`.

## packages

Each directory in `packages/` has a `build.sh OUTDIR` that builds one Ubuntu
Touch package with fixes for this port. It runs on arm64 Ubuntu 24.04 with the
UBports 24.04-2.x archive configured and preferred over Ubuntu's (see the
`packages` job in `build.yml`), and needs sudo for `apt-get build-dep`.

| Package | Why |
|---|---|
| `pulseaudio-modules-droid` | 14.2.109 with upstream's fix dfda983 for the startup crash on phones without a headphone jack. Upstream has the fix from 14.2.110, but UBports only ships that for 26.04 (PulseAudio 17), which can't load on 24.04-2.x. |

To build it on an arm64 machine:

```sh
packages/pulseaudio-modules-droid/build.sh workdir/extra-debs
```

To add a package, create `packages/<name>/build.sh` and a step in the
`packages` job.

## tools/make-debug-boot.sh

```sh
tools/make-debug-boot.sh BOOT_IMG OUTPUT_IMG [BREAK]
```

Makes a copy of a boot image that stops in the Halium initramfs.
`make-flashable.sh` uses it for `boot-installer.img`, and
[debugging](debugging.md) uses the same image. `BREAK` is an initramfs-tools
`break=` value; the default, `bottom`, stops after the rootfs and userdata are
mounted. The copy keeps the input's header, kernel, ramdisk and command line,
and adds `break=BREAK`. It gets an AVB hash footer for the 64 MiB boot
partition (`deviceinfo_bootimg_partition_size`), with a salt derived from the
image, so the same `boot.img` always gives the same output.

It needs python3 and git, and fetches AOSP's mkbootimg and avbtool at pinned
commits into `tools/.cache/`.

## Pinned versions

| What | Where | Pin |
|---|---|---|
| UBports build tools | `build.sh` `BUILD_TOOLS_REV` | `7dd99e0d` |
| halium-gki | `build.sh` `HALIUM_GKI_REV` | `e05c1ebd` |
| Kernel, `android14-6.1-halium` | `build.sh` `KERNEL_REV` | `994f2f42` |
| Pixel 9 port (`vndservicemanager`) | `build.sh` `ZUMAPRO_REV` | `02787eaf` |
| libhybris packages from 26.04-1.x | `build.sh` `LIBHYBRIS_VER`, `LIBHYBRIS_DEBS` | version and the sha256 of each package |
| Halium initramfs (`initrd.img-touch-arm64` of the `dynparts` release of initramfs-tools-halium) | `build.sh` `RAMDISK_URL`, `RAMDISK_SHA256` | sha256 |
| Clang | `deviceinfo` | `r487747c` (`main-kernel-build-2023`), as Google's android14-6.1 GKI builds |
| mkbootimg, avbtool | `tools/make-debug-boot.sh` | `d2bb0af5`, `c5066a96` |
| pulseaudio-modules-droid | `packages/pulseaudio-modules-droid/build.sh` | upstream `72273cc4` (14.2.109), UBports packaging `4ad1b209` |
| shellcheck, actionlint | `lint.yml` | 0.11.0 and 1.7.12, with sha256 |
| Python 3.8 container | `lint.yml` | image digest |

The `dynparts` release is a moving URL. `build.sh` downloads the initramfs
itself and checks its sha256, and the build tools then use that file instead
of downloading their own. If the file at the URL changes, the build fails until
the pin is updated.

Not pinned:

- The Ubuntu Touch rootfs and the Halium 16 GSI. `prepare-fake-ota.sh`
  downloads the last successful build from UBports' CI, so two builds of the
  same commit can differ. `build-info.txt` records which ones a build used.
- The Ubuntu and UBports packages installed on the CI runners.
- The helper tools UBports' build tools clone at branch heads: AOSP's prebuilt
  build-tools and kernel build-tools, avb, the GCC 4.9 prebuilts and
  LineageOS's mkbootimg.

The kernel commit is pinned because the Halium branch gets rebased. Before
moving it, check that the stock vendor modules of BP4A.251205.006 still match
the new kernel's symbol CRCs: `out/Module.symvers`, in the `device-bluejay`
artifact, has them.

## Making a release

1. In `CHANGELOG.md`, make sure the version's section is complete and replace
   `unreleased` with the date: `## [0.1.0] - 2026-10-20`. Commit and push.
2. Tag the commit and push the tag:

   ```sh
   git tag -a v0.1.0 -m v0.1.0
   git push origin v0.1.0
   ```

3. `release.yml` runs `lint.yml` and `build.yml` with the release variant. It
   then creates a **draft** release with `boot.img`, `boot-installer.img`,
   `system.img.zst`, `install.py`, `build-info.txt` and a `SHA256SUMS` over
   the other five. The release notes are the version's section of
   `CHANGELOG.md`, plus a line on how to install.
4. Test the draft's files on a phone, then publish the release on GitHub.
   Check what the changelog says, for example that adb asks on the screen and
   that there's no SSH during boot.

A tag with a suffix, such as `v0.2.0-rc1`, makes a pre-release. To run
`release.yml` again for the same tag (Re-run jobs in GitHub Actions), delete
the draft first with `gh release delete v0.1.0`: the workflow fails when the
release already exists.
