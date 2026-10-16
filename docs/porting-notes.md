# Porting notes

How this port works, and the reason for each decision that isn't obvious. The
sources are the commit history and the comments in `overlay/`, `kernel/`,
`deviceinfo` and the scripts. When you change one of these files, keep its
comment and this page in step.

## The base: Halium 16 on stock BP4A.251205.006

The phone keeps Google's firmware, vendor and vendor_dlkm partitions from stock
Android 16, build BP4A.251205.006. Ubuntu Touch runs the generic Halium 16
system image (GSI) in an LXC container on top of them.

Halium 16 is based on LineageOS 23.2, and LineageOS 23.2 for bluejay is based
on BP4A.251205.006. So the Halium system image matches the vendor partitions on
the phone. A newer Android build on the phone wouldn't match. Going back to
BP4A.251205.006 from a newer build is a downgrade, which the bootloader's
anti-rollback can refuse (see [installing](install.md), step 2).

The installer checks the bootloader version of that build,
`bluejay-16.4-14097577`, and refuses other versions. A different modem
firmware version only gives a warning.

## Kernel

UBports' generic GKI kernel, branch `android14-6.1-halium`, with
`gki_defconfig`, `halium.config` and `kernel/bluejay.config`. It's built with
Clang `r487747c`, the compiler of Google's android14-6.1 GKI builds.

**GKI ABI.** The stock kernel on BP4A.251205.006 is GKI
`6.1.145-android14-11`. The Halium branch is built on the same GKI branch and
keeps its ABI: every stock vendor module matches the Halium kernel's symbol
CRCs, and all 203 vendor_boot modules load. The Halium branch gets rebased, so
`build.sh` pins the commit whose ABI was checked against the stock vendor
modules.

**Bluetooth and rfkill as modules.** `halium.config` builds Bluetooth and
rfkill into the kernel. The stock kernel has them as modules
(`CONFIG_BT=m`, `CONFIG_RFKILL=m`), and vendor_dlkm ships `rfkill.ko`,
`bluetooth.ko`, `hidp.ko` and `rfcomm.ko`. With them built in, the kernel
rejects those modules ("exports duplicate symbol ... (owned by kernel)" or
"module is already loaded"). Android's libmodprobe then refuses every module
whose modules.dep entry lists `rfkill.ko`: cfg80211, mac80211, bcmdhd4389
(Wi-Fi), nitrous (Bluetooth power) and nfc. `bluejay.config` makes them
modules again.

`CONFIG_BT_HCIVHCI` is a module too. vendor_dlkm has no `hci_vhci.ko`, and
the GSI turns off module autoloading, so `bluebinder.service.d/bluejay-wait.conf`
loads it with modprobe once the vendor Bluetooth HAL is running.

## Boot image and initramfs

The phone launched with Android 12: boot image header v4, no `init_boot`
partition, and 64 MiB `boot_a` and `boot_b` partitions. `boot.img` holds the
Halium kernel and the Halium ramdisk. The bootloader loads that ramdisk on top
of the stock vendor_boot ramdisks, which carry the first-stage modules, so
vendor_boot stays stock. dtbo stays stock too.

The Halium initramfs (initramfs-tools-halium, `dynparts` release) maps the
logical partitions of the stock `super` partition with dmsetup. The stock
vendor partitions in it are used as they are. The `dynparts` release is a
moving URL, so `build.sh` downloads the file itself and pins it by sha256; the
build tools would take whatever the URL holds, unchecked.

### Trusty IPC and the module order

`trusty-virtio.ko` hands Trusty the descriptors of its virtio devices and
starts them. Trusty only accepts a device whose driver is ready, and
`trusty-ipc.ko` is that driver, so trusty-virtio has to load after trusty-ipc.
The module says so (`MODULE_SOFTDEP("pre: trusty-ipc")`, also in the stock
`modules.softdep`), and stock Android's first-stage init follows it.

The Halium initramfs starts udevd before it loads the modules in
`modules.load`. trusty-core loads early, as a dependency of `ehld`, and adds
the `trusty:virtio` platform device. udevd then loads trusty-virtio by its
alias while modprobe is still loading trusty-ipc in the same dependency chain.
libkmod counts a module that is still initializing as loaded, so the soft
dependency doesn't always hold. On some boots trusty-virtio started the device
before trusty-ipc had registered its driver. Trusty logged
`validate_descr:1158: unexpected status 1` in `/dev/trusty-log0` and never
started IPC, while Linux showed `virtio0` as bound and ready (status 7).
Without Trusty IPC:

- the GSA can't authenticate the AoC firmware, so AoC stays `Offline` and
  there's no sound
- KeyMint and metricsd restart in a loop

In testing this happened on 3 of 5 first boots after an install, and not on
ordinary restarts.

`ramdisk-overlay/lib/udev/rules.d/79-bluejay-trusty-virtio.rules` clears the
modalias of the `trusty:virtio` device in the initramfs, so udevd's
`80-drivers.rules` doesn't load the module. The initramfs then loads
trusty-virtio by name at its place in `modules.load`, about a second later.
By then trusty-ipc has long been loaded, and modprobe follows the soft
dependency there anyway. A blacklist (`modprobe.blacklist=trusty_virtio`)
doesn't work for this: halium-gki's loader runs modprobe with `-b`, which
applies the blacklist to module names too. The module then waited for the
vendor `insmod_sh` service at about 8.7 s, after KeyMint had started.

### Slot a only

The Halium initramfs reads the A/B slot suffix only from the kernel command
line (`androidboot.slot_suffix=` in `/proc/cmdline`), and falls back to `_a`
when it isn't there. This bootloader passes `androidboot.*` values through
bootconfig instead (`/proc/bootconfig` has `androidboot.slot_suffix = "_a"`).
So the port lives on slot a: the installer flashes only `boot_a` and refuses
a phone that runs from slot b.

## Rootfs layout: ubuntu.img on userdata

The rootfs is a raw 5 GiB ext4 file, `/userdata/ubuntu.img`. The Halium
initramfs mounts userdata, finds `ubuntu.img` at its root and loop-mounts it.
The Halium GSI is a file inside the rootfs,
`/var/lib/lxc/android/android-rootfs.img`, and is loop-mounted too.

This is UBports' legacy layout. The port's first install steps flashed the
rootfs into the `system` logical partition from fastbootd instead (and deleted
`product_a` to make room). It moved to the legacy layout because:

- fastbootd doesn't work from the developer's Mac: `fastboot` fails with
  `usb_write` error `e00002eb`. This layout needs only the bootloader.
- The stock `super` partition stays untouched, so the stock vendor partitions
  in it are used as they are, and no logical partition has to be deleted or
  resized.

The initramfs checks userdata with its own `e2fsck -y` at every boot.

## libhybris for Android 16

The first boot reached systemd and the Halium 16 container, but every
libhybris user (`lxc-android-ready`, usb-moded's configurator, ...) aborted in
the linker's TLS code. Neither USB nor the user interface came up.

**libhybris from 26.04-1.x.** The Android 16 libraries use ELF TLS (TLSDESC).
The libhybris in 24.04-2.x can't load them yet; the one in the 26.04-1.x
archive can
([libhybris MR 65](https://gitlab.com/ubports/development/core/packaging/libhybris/-/merge_requests/65)).
`build.sh` downloads those packages, pinned by version and sha256, and puts
them in the rootfs overlay. Drop this once 24.04-2.x ships it.

**Static TLS (`GLIBC_TUNABLES`).** That libhybris needs about 1.7 KB of static
TLS (its linker `q.so` alone has 1568 bytes of initial-exec TLS), more than
glibc's default surplus. Every process that loads it needs
`GLIBC_TUNABLES=glibc.rtld.optional_static_tls=4096`, the value upstream
lxc-android-config uses:

- system and user services: `DefaultEnvironment` in
  `/etc/systemd/system.conf.d/` and `/etc/systemd/user.conf.d/`
  (`90-hybris-tls.conf`)
- login shells: `/etc/profile.d/hybris_tls_glibc_tunables.sh`
- the compositor: `/etc/default/lsc-wrapper.d/60-bluejay.conf`, because
  lightdm starts it with a cleared environment

`/etc/gbinder.conf` sets `ApiLevel = 36` (Android 16) for libgbinder.

## Vendor services

### vndservicemanager

The stock BP4A.251205.006 `vndservicemanager` checks SELinux
(`getPidcon()`, `selinux_status_open()`) and aborts without it, which takes
every vendor HAL down with it. The port uses the Pixel 9 port's build, made
with Halium's `0005-halium-disable-SELinux-checks-in-ServiceManager.patch`.
`build.sh` copies the binary from the pinned Pixel 9 port into the Halium
overlay (`/usr/share/halium-overlay/vendor/bin/`), and this repository's
`vndservicemanager.rc` starts it with `libselinux_stubs.so` preloaded.

### Waiting for the HALs

The vendor HALs start in the container at their own pace, often after the
Ubuntu Touch services that use them. The systemd drop-ins wait until
`getprop init.svc.<service>` says `running`. Every wait is bounded and ends
before the unit's start timeout:

| Ubuntu Touch service | Waits for | At most | Why |
|---|---|---|---|
| sensorfwd | `vendor.sensors-hal-multihal` (AIDL sensors v3) | 75 s | Without it every adaptor fails until a restart |
| hfd-service | `/dev/binderfs/binder`, then `vendor.vibrator.cs40l26` (AIDL) | 20 s, 60 s | The vendor service only starts on `vendor.all.modules.ready=1`, so hfd's auto-detection could miss it and fall back to the force-feedback input device; `HFD_VIBRATOR_IMPL=binder-aidl` selects it |
| bluebinder | `bcmbtlinux` (AIDL IBluetoothHci) | 50 s | `bluebinder_wait.sh` looks for `init.svc.*bluetooth*`, but this HAL is named `bcmbtlinux` |
| lomiri-system-compositor (lsc-wrapper) | `vendor.hwcomposer-2-4` and `vendor.graphics.allocator-default` | 90 s | lightdm's compositor timeout is raised to 150 s to match (below) |

### USB gadget HAL

usb-moded on the host owns the USB gadget. Halium's init doesn't mount
configfs at `/config` in the container, so the Pixel USB gadget HAL aborts at
start ("configfs setup not done yet"), and init restarts it in a loop. Nothing
in the container calls it (there is no system_server), so the Halium overlay
keeps the service defined but disabled.

## The Android container

The LXC mount hook, `overlay/system/var/lib/lxc/android/mount.sh`, prepares
the container's `/dev` (a tmpfs at that point) and `/proc`.

**`/proc/sysrq-trigger`.** Build 2 got usb-moded running, but adbd never
answered. The first-boot journal showed why: Android init's shutdown path
(`reboot_on_failure` of apexd and vold, critical services) writes to
`/proc/sysrq-trigger`. That file isn't namespaced: sysrq `e` and `i` signal
every process on the host, not only the container's. On the first boot
journald restarted and `lxc-android-ready` got SIGTERM at 39.7 s.
`kernel.sysrq=0` doesn't help, because it only gates the keyboard (and the
GSI's init.rc already writes 0 there). The hook bind-mounts an empty file over
the container's `/proc/sysrq-trigger`. Not `/dev/null`: init.rc changes the
owner and mode of `/proc/sysrq-trigger`, which would change the host's
`/dev/null`.

**Trusty.** Trusty, the TEE behind KeyMint, Gatekeeper and metricsd, is reached
through `/dev/trusty-ipc-dev0`. The host has it (with a dynamic major number)
and `/dev/trusty-log0`, but the container's ueventd never creates them, and
KeyMint and metricsd fail with "tipc_connect: cannot open tipc device
/dev/trusty-ipc-dev0: No such file or directory". The hook creates both nodes
from the numbers in sysfs, with the owners and modes of the stock ueventd.rc:
`trusty-ipc-dev0` 0660 system:drmrpc (1000:1026), `trusty-log0` 0660
system:system.

**device-hacks** (`/usr/libexec/lxc-android-config/device-hacks`):

- Android 16's libcutils opens `/dev/ashmem<boot_id>`, which the container's
  ueventd creates only inside the container. The GSI's libcutils falls back,
  but vendor copies loaded into host processes (the audio HAL inside
  PulseAudio) don't, so the script links it to `/dev/ashmem`.
- Codec2 and droidmedia clients on the host allocate from the DMA-BUF heaps
  `system` and `system-uncached`, so they are made world-writable.
- Unlike the Pixel 9 port, it doesn't bind-mount the vendor libc++ over the
  GSI's: on BP4A.251205.006 the two files are identical.

## Display

`/etc/default/lsc-wrapper.d/60-bluejay.conf`, modelled on the Pixel 9 port:

- `MIR_ANDROID_FORCE_HWC2=1`: never open `hwcomposer.gs101.so` in-process;
  the vendor composer service owns it.
- `GLIBC_TUNABLES` (above).
- `HYBRIS_LD_LIBRARY_PATH=/vendor/lib64/hw` (temporary): libui (Gralloc5) and
  `libGLES_mali.so` load the mapper `mapper.pixel.so` from `/vendor/lib64/hw`,
  which the hybris linker doesn't find through linkerconfig. Login shells get
  it from `/etc/profile.d/hybris_vendor_hw_path.sh`.
- It waits up to 90 s for the composer and the allocator. lightdm's default
  compositor timeout is 60 s, after which lightdm exits and systemd restarts it
  (seen every ~65 s on an early boot), so `lightdm.conf.d/60-bluejay.conf`
  raises it to 150 s.

**Mali and `pid_max`.** `libGLES_mali.so` names its kbase sync timeline
`malitl_%d_%p` in a 32-byte buffer. Process IDs with more than 5 digits make
OpenCL and EGL context creation fail, so `sysctl.d` sets
`kernel.pid_max = 99999` (from the Pixel 9 port).

**Device description.** `/etc/deviceinfo/devices/bluejay.yaml`: grid unit 21
from the panel density 420, and the display cutout and status bar height from
the vendor's framework overlay.

## Wi-Fi: aware_nmi0 and a kernel panic

On build 2 the phone restarted a few seconds after reaching the home screen,
and then on every boot. NetworkManager had joined the saved network through
`aware_nmi0`, the Wi-Fi Aware (NAN) interface of bcmdhd4389. When cfg80211
later disconnected it, the stock driver dereferenced NULL in
`wl_cfg80211_disconnect` and the kernel panicked. The panic was in the pstore
console log (see [debugging](debugging.md#kernel-panics-and-restarts-pstore)).

NetworkManager now leaves `aware_nmi*` and the second station interface
`wlan1` unmanaged and only manages `wlan0`, as the Pixel 9 port does. The
port also takes the Pixel 9 port's hotspot setting: with a password on
bcmdhd, don't advertise optional PMF for WPA-PSK.

## Sound

PulseAudio failed on the phone in two ways.

**Float outputs.** The stock audio policy declares the "primary output" and
"deep buffer" mix ports as `AUDIO_FORMAT_PCM_FLOAT`. PulseAudio's droid module
can't use them ("Couldn't find compatible configuration for mix port primary
output") and then has no sink at all. The gs101 audio HAL opens the same
outputs in 16-bit PCM. `bluejay-audio-policy.service` runs before the
container starts: it writes a copy of the phone's own
`/android/vendor/etc/audio_policy_configuration.xml` with
`AUDIO_FORMAT_PCM_16_BIT` to `/run/bluejay/` and bind-mounts it over the
vendor file. It's generated at boot, so it follows the vendor partition.

**Crash without a headphone jack.** The droid module's extevdev finds no input
device on a phone without a headphone jack, and its error path frees a NULL io
event, which aborts the daemon. Upstream fixed this in dfda983 (14.2.110), but
UBports only ships that version for 26.04 (PulseAudio 17), which can't load on
24.04-2.x. `packages/pulseaudio-modules-droid/build.sh` builds the 24.04-2.x
module (UBports' packaging, at the version on the phone) with the upstream fix.
CI builds it on an arm64 runner, and `build.sh` adds it to the rootfs overlay.

## USB

USB is managed by usb-moded and UBports' configurator, with settings in
`/etc/default/usb-moded.d/bluejay.conf` and
`/etc/usb-moded/90-device-specific-config.ini`.

- **CDC-NCM.** The Halium kernel has `CONFIG_USB_CONFIGFS_NCM=y` and no RNDIS,
  so the configurator's autodetection ends on CDC-NCM, which macOS supports
  natively.
- **No MTP.** The kernel has no MTP gadget function (no `f_mtp`), and Ubuntu
  Touch's mtp-server needs `/dev/mtp_usb`, so the MTP modes can't work. The
  default mode is charging only, and the MTP modes are hidden. Otherwise
  force-adb saves `mtp` on the first boot (no passcode yet), and Developer Mode
  would then ask for `mtp_adb`, which fails. Developer Mode uses
  `charging_only_adb` instead.
- **Pixel USB IDs.** Google's vendor ID 0x18d1 with the product IDs stock
  Android uses for the same function sets. Without them the configurator falls
  back to pid.codes test IDs.
- **No getprop.** The configurator runs under `sh -eu`, and an aborting
  `getprop` made usb-moded fail with status 134. `bluejay.conf` sets
  everything it would ask getprop for (manufacturer, product, the controller
  `11110000.dwc3`), so USB doesn't depend on libhybris or the container. The
  vendor sets `sys.usb.controller` only in charger mode anyway.

Rescue mode and the marker files are covered under
[Devel and release images](#devel-and-release-images).

## Modem

The modem is a Samsung Shannon s5123 with the vendor's RIL. The vendor
registers HIDL `android.hardware.radio@1.6::IRadio/slot1` and `slot2` on
`/dev/hwbinder`. ofono-binder-plugin 1.1.33 and libgbinder-radio speak HIDL up
to 1.5, and a 1.6 service is also registered under its 1.5 parent, so
`/etc/ofono/binder.d/bluejay.conf` uses `radioInterface = 1.5`. Each slot needs
its own section with a path, or the plugin creates no modem. Both SIM slots
come up in oFono; calls and mobile data haven't been tested.

## udev rules and AppArmor

`/usr/lib/udev/rules.d/70-bluejay.rules` was generated from the ueventd.rc
files of the Halium 16 GSI and the stock vendor, with the UBports
porting-guide method, then curated by hand following the Pixel 3a port:

- Android groups that don't exist in Ubuntu Touch are mapped to the Ubuntu
  Touch group with the same numeric ID (graphics 1003 to `android_graphics`,
  and so on), or dropped.
- ueventd paths in subdirectories never match udev's `KERNEL==`, so they are
  rewritten with `SUBSYSTEM==` or dropped.
- Nodes that only the container's services open (camera, codecs, TPU, NFC,
  Titan M, Trusty, modem diagnostics) are left to the container's ueventd.
  They are listed in comments, so the file can be compared with ueventd.rc.
- Rules that would weaken the host are dropped.

halium-gki ships `70-mtk-common.rules` and `70-qcom-common.rules` for all GKI
devices. On gs101 they only add noise or harm the host, so this port replaces
them with empty files.

The AppArmor hardware profile
`apparmor-easyprof-ubuntu_android16` lets confined apps read the Android 16
APEX libraries and the DMA-BUF heaps (from the Pixel 9 port).

## No recovery

UBports' unified recovery has no USB or fstab setup for gs101 yet, so
`deviceinfo` turns it off. A recovery boot falls through to the normal Halium
boot and starts Ubuntu Touch.

## The installer

`tools/install.py` installs over the bootloader and a shell in the Halium
initramfs, because of the layout above and these facts about the phone:

- fastbootd doesn't work from the developer's Mac.
- `fastboot boot` doesn't work on this phone, so the installer image has to
  be flashed to `boot_a`.
- From Ubuntu Touch there's no way into the bootloader without root:
  `adb reboot bootloader` fails. From stock Android it works. The installer
  offers it only for a device whose `ro.product.device` is `bluejay`, so it
  never restarts another Android device that happens to be connected.

### The installer shell

`boot-installer.img` is `boot.img` with `break=bottom` on the kernel command
line (made by `tools/make-debug-boot.sh`). The Halium initramfs then stops in
its panic handler, which sets up a USB CDC-NCM network gadget with the phone
at 192.168.2.15, runs udhcpd to give the computer an address in
192.168.2.20-90, and runs busybox telnetd on port 23 with a root shell.

The installer talks to that shell with its own minimal telnet client. It
refuses every telnet option, so the connection is a plain byte stream. It runs
each command between unique begin and end markers, and the end marker carries
the exit status:

```sh
echo __B''7__; <command>; echo __E''7__$?
```

The quotes keep the shell's echo of the command line from looking like the
markers. Terminal escapes (the line editor asks for the cursor position with
`ESC[6n`) and carriage returns are stripped before the output is parsed.

Before it changes anything on the phone, the installer checks the shell:

- `break=bottom` in `/proc/cmdline`, so it is talking to the installer and not
  to another device at that address,
- `androidboot.serialno` in `/proc/bootconfig` against the serial number
  fastboot reported, so the shell runs on the phone that passed the checks in
  fastboot mode (192.168.2.15 is the same on every phone),
- the tools it needs: `mke2fs`, `nc`, `sha256sum`, `dd`, `mount`, `umount`,
  `setsid` and `blkid`. A missing tool stops it before it formats userdata.

If userdata is ext4 with `ubuntu.img`, the initramfs mounts the rootfs at
`/root` and userdata at `/root/userdata` before it stops. If userdata is
Android's encrypted f2fs, nothing is mounted and the shell comes from the
panic path.

### Formatting userdata

A fresh install formats userdata with the initramfs's own mke2fs
(e2fsprogs 1.43). The initramfs runs its e2fsck on userdata at every boot, and
a filesystem made by a newer mke2fs can have features it doesn't know, such as
`orphan_file`. UBports' image builder also turns `orphan_file` off in the
rootfs image it makes.

Everything that uses userdata has to be released first. In a normal boot
userdata is mounted at `/root/userdata`, inside the rootfs image that is stored
on userdata, so neither can be unmounted first. The installer turns off swap
files, moves the userdata mount out of the way (`mount --move` to `/tmpmnt`),
unmounts everything else deepest first, and unmounts userdata last. It retries
for about 15 seconds, because the kernel releases a loop device shortly after
its unmount, and only then the file on userdata behind it.

### Copying the image

The installer starts busybox `nc -l -p 5555` on the phone, detached with
setsid, writing `ubuntu.img.new`. The computer streams the raw ext4 image: it
expands the Android sparse image on the fly and decompresses `system.img.zst`
with `zstd -dc`, so there's no temporary copy on the computer. It computes the
SHA-256 while it sends. The phone then runs `sha256sum` on the copy, and only
if both match does the installer move it over `ubuntu.img`. The image is 5 GiB:
about 2 minutes at about 40 MB/s, and about 70 s for `sha256sum` on the phone.

It writes `ubuntu.img.new` and renames it because on an installed phone the old
`ubuntu.img` is the mounted rootfs; the old file stays usable until the phone
restarts.

### Back to the bootloader

The shell's `reboot` can't pass a reason. The installer writes
`bootonce-bootloader` into the 32-byte command field of the bootloader control
block at the start of `misc`, reads it back, syncs and runs `reboot -f`. The
bootloader starts in fastboot mode (`enter-reason` is `bootonce-bootloader`).
The installer then flashes `boot.img` to `boot_a` and restarts the phone.

### What it never touches

The installer only flashes `boot_a`; its fastboot wrapper refuses any other
partition. It never runs `fastboot erase`, `format`, `--set-active` or `-w`.
On the phone it writes only userdata and the command field in `misc`. The
bootloader, modem firmware, vbmeta and `super` stay stock, so going back to
Android is a normal factory image flash. If a fresh install stops before it
formats userdata, Android's data is still there and only `boot_a` changed:
flashing the factory image's `boot.img` brings Android back with its data,
and the installer's error message says so.

Before it changes anything, it checks the files against `SHA256SUMS`, checks
that `boot-installer.img` has `break=bottom` and `boot.img` has no `break=`
(so swapped files are caught), and checks the phone's bootloader variables.

## Devel and release images

`make-flashable.sh --variant devel|release` adds the files of
`overlay-devel/` or `overlay-release/` as the last tarball of the image.

**adb.** UBports' `prepare-fake-ota.sh` writes `ADBD_SECURE=0` into the rootfs
so that adb needs no authorization while porting. Release images replace
`/etc/default/adbd` with `ADBD_SECURE=1`, so adb asks on the phone, as stock
Ubuntu Touch does.

**Rescue mode.** `usb-moded.service` sets `USB_MODED_ARGS=-r` (rescue mode),
then reads `/etc/default/usb-moded.d/*.conf` in sorted order, which can
override it. In rescue mode the phone offers USB networking and SSH, with
empty passwords allowed, until `usb-rescue-mode-off.service` runs. That
happens as soon as lightdm has started, not when the user interface is up.
Until then, anyone with a cable could log in. Release images add
`zz-bluejay-release.conf` with `USB_MODED_ARGS=""`, which sorts after
`bluejay.conf` and the rootfs's `device-specific-config.conf`. An empty value
passes no arguments to usb_moded, so rescue mode is off. Devel images keep it
for bring-up.

**Marker files.** UBports' `force-adb` runs after rescue mode ends. With
`/userdata/.force-adb` it switches USB to `charging_only_adb`; with
`/userdata/.force-ssh`, to `developer_mode` (USB networking and SSH on
10.15.19.82, port 8022). These are how porters get access without changing
the image. `install.py --dev-access` creates and removes them.

Removing a marker isn't enough to turn access off. `force-adb` sets the mode
with usb-moded's `set_config`, and usb-moded saves it in
`/var/lib/usb-moded/usb-moded.ini`. The rootfs's `writable-paths` makes that
directory persistent on userdata
(`/userdata/system-data/var/lib/usb-moded/`), so the saved mode survives
restarts and `--keep-data` upgrades. Without a marker, `force-adb` changes
nothing when `phablet` has a password, and in any case it deliberately keeps
`developer_mode`, whose SSH server accepts empty passwords. So every
`--dev-access` value also deletes `usb-moded.ini`. usb-moded then falls back
to `mode=charging_only` from `90-device-specific-config.ini`, unless a marker
asks for more. This hasn't been checked on the phone yet.

**Kernel messages.** Ubuntu Touch's journald configuration
(`lxc-android-config.conf`) sets `ReadKMsg=no`. Devel images add
`zz-bluejay-bringup.conf`, which sorts after it and sets `ReadKMsg=yes`, so the
journal keeps kernel messages and with them the container's init log.

**Upstart leftovers.** `prepare-fake-ota.sh` also writes
`/etc/init/ssh.override` (an sshd that allows empty passwords) and
`/etc/init/usb-tethering.conf`. Nothing reads `/etc/init` under systemd, so
they have no effect. A tarball can't delete them, because
`system-image-from-ota.sh` applies a tarball's list of removed files only to
images that don't start with `format system`. So release images replace both
with comment-only files (`overlay-release/system/etc/init/`); devel images keep
them.

## System updates

The version tarball from `prepare-fake-ota.sh` points the system updater at
UBports' server (`system-image.ubports.com`, channel
`24.04-2.x/arm64/android9plus/daily`, device `bluejay`). UBports has no images
for this unofficial port, so the updater finds nothing. Updates come as
releases of this repository and are installed with
`python3 install.py --keep-data`.
