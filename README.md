# Ubuntu Touch for the Google Pixel 6a (bluejay)

An unofficial [Ubuntu Touch](https://ubports.com) port for the Google Pixel 6a.

> **Status: early bring-up.** Ubuntu Touch boots to its welcome wizard, and
> touch, Wi-Fi scanning, rotation and vibration work. Don't install this on a
> phone you depend on: installing it wipes the phone, and calls, camera and
> other hardware may never work.

## Progress

Tested on build 3 (2026-10-15):

| Area | State |
|---|---|
| Kernel and first-stage drivers (UFS, USB, display, power, modem) | Working: all 203 vendor_boot modules load |
| Display, GPU (Mali-G78), Lomiri | Working |
| Touch | Working |
| Wi-Fi | Working (connects, internet) |
| Sensors (rotation) | Working |
| Vibration | Working |
| Battery and charging | Working |
| adb over USB | Working |
| Bluetooth | Controller up; pairing not tested |
| Modem | Both SIM slots come up in oFono; calls and data not tested |
| Sound | Not yet: PulseAudio's droid module rejects the float outputs and then crashes (fixes in progress) |
| Camera, fingerprint, GPS | Not tested |

## How it works

This is a Halium GSI port, built the same way as other UBports community ports:

| Part | Source |
|---|---|
| Firmware, vendor and vendor_dlkm | Stock Android 16 from Google, build **BP4A.251205.006**, left untouched on the phone |
| Kernel | UBports' generic GKI kernel, branch [`android14-6.1-halium`](https://gitlab.com/ubports/porting/community-ports/android12/generic/kernel-android-common/-/tree/android14-6.1-halium), pinned in `build.sh`, plus `kernel/bluejay.config` |
| Android system image (in an LXC container) | Generic Halium 16 GSI built by UBports CI |
| Rootfs | Ubuntu Touch 24.04-2.x, with libhybris from the 26.04-1.x archive |
| Shared GKI ramdisk and rootfs overlays | UBports' [halium-gki](https://gitlab.com/ubports/porting/community-ports/android12/generic/halium-gki) port |
| Patched `vndservicemanager` | UBports' [Pixel 9 port](https://gitlab.com/ubports/porting/community-ports/android16/google-pixel-9/google-zumapro) |
| Device overlay (`overlay/`) | This repository |

The stock kernel on BP4A.251205.006 is GKI `6.1.145-android14-11`. The Halium
kernel is built on the same GKI branch and keeps its ABI: every stock vendor
module matches the Halium kernel's symbol CRCs. Halium 16 is based on
LineageOS 23.2, and LineageOS 23.2 for bluejay is based on the same
BP4A.251205.006 build, so the Halium system image matches the vendor partitions
on the phone.

**Keep the phone on BP4A.251205.006.** A newer Android version on the phone
won't match the Halium 16 system image.

## Building

The build runs on Linux (x86_64). GitHub Actions builds every push to `main`
and uploads `boot.img` and `system.img.zst` as artifacts. See
[`.github/workflows/build.yml`](.github/workflows/build.yml).

```sh
./build.sh            # kernel, boot.img and device tarball, into out/
./make-flashable.sh   # out/boot.img and out/system.img (needs sudo)
```

`build.sh` pins the versions of everything it fetches. The files in this
repository's `overlay/` are applied on top of halium-gki's.

## Installing (development)

This install doesn't use fastbootd and doesn't change the `super` partition, so
stock Android stays on the phone. The rootfs lives in `/userdata/ubuntu.img`,
which the Halium initramfs loop-mounts. You need `fastboot` and `adb` from a
recent Android platform-tools, `telnet` and Python 3. It works from macOS
(USB networking uses CDC-NCM).

1. Make sure the phone runs stock BP4A.251205.006 (flash Google's factory image
   if not), then unlock the bootloader. **This wipes the phone:**

   ```sh
   fastboot flashing unlock
   ```

2. Format userdata as ext4 and boot a debug image that stops in the initramfs
   shell:

   ```sh
   fastboot format:ext4 userdata
   tools/make-debug-boot.sh boot.img boot-debug.img
   fastboot flash boot_a boot-debug.img
   fastboot reboot
   ```

   The phone shows up as a USB network device, and `telnet 192.168.2.15` gives a
   root shell. The screen keeps showing the Google logo.

3. Copy the rootfs. In the telnet shell:

   ```sh
   nc -l -p 5555 > /root/userdata/ubuntu.img
   ```

   On the computer (expand `system.img.zst` with `zstd -d` first):

   ```sh
   tools/send-sparse.py system.img 192.168.2.15 5555
   ```

   Compare the SHA-256 it prints with `sha256sum /root/userdata/ubuntu.img` in
   the shell. For development, also create `/root/userdata/.force-adb` (adb) or
   `/root/userdata/.force-ssh` (USB networking and SSH).

   To reinstall over an existing image, write to `ubuntu.img.new` and `mv` it
   over `ubuntu.img` after checking it: the debug shell has the old image
   mounted.

4. Go back to the bootloader from the shell and flash the real boot image:

   ```sh
   printf 'bootonce-bootloader' | dd of=/dev/disk/by-partlabel/misc bs=32 count=1 conv=notrunc,sync
   reboot -f
   ```

   ```sh
   fastboot flash boot_a boot.img
   fastboot reboot
   ```

Install on slot a only (`fastboot getvar current-slot` should say `a`): the
initramfs always uses slot a for now.

### Debugging

- Until lightdm starts, usb-moded's rescue mode offers USB networking and SSH:
  `ssh -p 8022 phablet@10.15.19.82`. With `/userdata/.force-ssh` that mode
  stays on.
- With `/userdata/.force-adb`, USB switches to adb once lightdm has started.
  Development images don't ask to authorize the computer (`make-flashable.sh`
  builds them with `ADBD_SECURE=0`).
- To read the logs of a boot that didn't come up, flash the debug image from
  step 2 again. In its shell, the journal is under
  `/root/userdata/system-data/var/log/journal`:

  ```sh
  chroot /root journalctl -D /userdata/system-data/var/log/journal -b -1 --no-pager
  ```

  During bring-up the journal also keeps kernel messages, and with them the
  Android container's init log (`journald.conf.d/zz-bluejay-bringup.conf`).

### Going back to Android

Flash Google's factory image for BP4A.251205.006 with `flash-all.sh`. It
restores the boot image and wipes userdata.

## Known limitations

- MTP doesn't work: the GKI kernel has no MTP gadget function. USB defaults to
  charging only; Developer Mode gives adb.
- No recovery: a recovery boot starts Ubuntu Touch instead.
- fastbootd (`fastboot reboot fastboot`) doesn't respond to some Macs; the
  install above doesn't need it.
- Expect no VoLTE, and the under-display fingerprint reader isn't supported by
  Ubuntu Touch 24.04.

## Credits

This port is built on the work of the UBports and Halium communities: the
build tools, the GKI kernel and ramdisk, the Halium GSI, libhybris, Ubuntu
Touch itself, and the Pixel 9 (zumapro) port, whose Android 16 and Tensor
fixes this port follows.

## License

Files in this repository are licensed under the GNU General Public License
v3.0 or later. See [LICENSE](LICENSE). Components fetched during the build keep
their own licenses.
