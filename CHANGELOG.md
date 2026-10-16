# Changelog

All notable changes to this port are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
The release workflow (.github/workflows/release.yml) uses the section of the
tagged version as the release notes. Before tagging a version, rename
"## [Unreleased]" to the version and its release date, for example
"## [0.2.0] - 2026-11-02", and add a new empty "## [Unreleased]" above it.
-->

## [Unreleased]

## [0.1.0] - 2026-10-16

The first release: Ubuntu Touch 24.04-2.x for the Google Pixel 6a, with
Halium 16 on stock Android 16 build BP4A.251205.006. This is an alpha.

### Added

- Ubuntu Touch 24.04-2.x on the Pixel 6a. Working: display and GPU (Lomiri),
  touch, Wi-Fi, Bluetooth, sound output and volume keys, microphone, front and
  back cameras with video recording, GPS, rotation sensor, vibration, battery
  and charging, and adb.
- `install.py`, an installer for macOS and Linux. It needs Python 3.8 or newer
  and `fastboot` (and `zstd` for `system.img.zst`), and it doesn't use
  fastbootd. It checks the downloaded files
  against `SHA256SUMS` and the phone's bootloader, slot and battery before it
  changes anything. Before it touches userdata, it checks that the installer
  shell runs on the phone it checked in fastboot mode (by serial number) and
  has the tools it needs. It offers `adb reboot bootloader` only for a
  Pixel 6a.
- `install.py --keep-data` installs a new release over an existing install and
  keeps your files and settings.
- `install.py --dev-access adb|ssh|none` turns adb or USB networking with SSH
  on, or both off. It also deletes the USB mode usb-moded saved on userdata,
  so `none` should turn access off even after an earlier `adb` or `ssh` (not
  yet checked on the phone). With
  `ssh`, it warns that SSH accepts `phablet` without a password until a
  passcode is set.
- When the installer stops before it formats userdata, it explains that
  flashing the factory image's `boot.img` brings Android back with its data.
- Release images: adb asks you to allow each computer on the phone's screen,
  usb-moded's rescue mode (USB networking with SSH during boot) is off, and the
  journal doesn't keep kernel messages.
  The unused upstart jobs `/etc/init/ssh.override` and
  `/etc/init/usb-tethering.conf` are replaced with comment-only files.
  Development images keep all of these for porting.
- Every build writes `build-info.txt`: the variant, the port's commit, the
  UBports rootfs and Halium GSI build numbers and the SHA-256 of the
  downloaded inputs. It comes with the CI images and each release, and
  `SHA256SUMS` lists it.
- `build.sh` pins the Halium initramfs by sha256.
- Documentation: installing, building, debugging and porting notes in `docs/`.

### Fixed

Compared with the development builds:

- The phone no longer panics and restarts after it joins a saved Wi-Fi network.
  NetworkManager now manages only `wlan0`, not the Wi-Fi Aware interface
  `aware_nmi0`.
- Sound works: PulseAudio no longer crashes at startup on a phone without a
  headphone jack, and it opens the speaker outputs (16-bit audio policy).
- Android services in the container can no longer signal processes of Ubuntu
  Touch itself through `/proc/sysrq-trigger`.
- The Android container now has the Trusty device nodes
  (`/dev/trusty-ipc-dev0`, `/dev/trusty-log0`) that KeyMint, Gatekeeper and
  metricsd open.
- Sound no longer fails on some boots, most often the first one after an
  install. The initramfs could start Trusty's virtio device before its IPC
  driver was ready. Trusty then refused IPC for the whole boot, so the audio
  processor's firmware couldn't load and KeyMint restarted in a loop. udev in
  the initramfs no longer loads `trusty-virtio`; the initramfs loads it from
  its module list, after `trusty-ipc`.

### Known issues

- The phone must run stock Android BP4A.251205.006. Other Android builds don't
  match the Halium 16 system image.
- Going back to BP4A.251205.006 from a newer build is a downgrade. The
  Pixel 6a has bootloader anti-rollback: if a later update raised the
  anti-rollback version, the phone can't go back to this build and can't run
  this release. See the install guide, step 2, before you downgrade.
- Only slot a is supported.
- No deep sleep: the phone doesn't suspend. With the screen off, Wi-Fi on and
  no SIM it used about 1.3% of the battery per hour overnight.
- `install.py` was tested on the phone over an existing install. Installing
  straight from Android uses the same steps but hasn't been run with the
  script yet.
- Calls and mobile data haven't been tested.
- Bluetooth audio devices and the Wi-Fi hotspot haven't been tested.
- File transfer over USB (MTP) doesn't work: the kernel has no MTP gadget
  function. USB is in charging-only mode by default.
- The fingerprint reader isn't supported.
- No system updates from UBports' servers: the system updater finds nothing.
  New versions come as releases of this repository; install them with
  `python3 install.py --keep-data`.
- No recovery: a recovery boot starts Ubuntu Touch.
- Security: the bootloader stays unlocked and userdata isn't encrypted. Anyone
  who has the phone and a USB cable can flash a boot image and read all data.
- Flashing Google's factory image with `flash-all.sh` (install guide step 2,
  and going back to Android) needs fastbootd, which doesn't answer on some
  Macs. Use a Linux computer for that step.

[Unreleased]: https://github.com/logicalangel/ubuntu-touch-bluejay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/logicalangel/ubuntu-touch-bluejay/releases/tag/v0.1.0
