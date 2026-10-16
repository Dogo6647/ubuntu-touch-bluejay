# Debugging

This page is for porters and testers: how to get a shell on the phone, where
the logs are, and how to read the logs of a boot that didn't come up.

## Devel and release images

Debugging is easiest with a devel image, the variant CI builds for every push
(see [building](building.md#image-variants)):

| | devel | release |
|---|---|---|
| adb | No authorization | Asks on the phone's screen |
| USB rescue mode (USB networking and SSH until lightdm starts) | On | Off |
| Kernel messages, and with them the Android container's init log, in the journal | Yes | No |

Devel images make it easy for anyone with a USB cable to get in: never give
them to users.

## Shell access on a running phone

**adb.** Once the user interface has started, `adb shell` works when adb is on
(Developer Mode or `/userdata/.force-adb`). Use `sudo -i` for a root shell; it
asks for the passcode.

**SSH.** In usb-moded's `developer_mode` USB mode the phone offers USB
networking and an SSH server. (This isn't the Developer Mode switch in System
Settings, which turns on adb.) The computer gets an address by DHCP:

```sh
ssh -p 8022 phablet@10.15.19.82
```

The server accepts the passcode as the password, and an empty password when no
passcode is set. Devel images are in `developer_mode` during boot, until
lightdm starts (usb-moded's rescue mode). With `/userdata/.force-ssh` the phone
stays in `developer_mode`.

**Marker files.** UBports' `force-adb` service runs after
`usb-rescue-mode-off.service`, which runs once lightdm has started. It switches
USB to:

- `charging_only_adb` (adb) when `/userdata/.force-adb` exists,
- otherwise `developer_mode` (USB networking and SSH) when
  `/userdata/.force-ssh` exists.

usb-moded saves the mode it is switched to in
`/userdata/system-data/var/lib/usb-moded/usb-moded.ini` and keeps using it
after the marker file is gone. Every `install.py --dev-access` value deletes
that file, so the image's default, `charging_only`, should apply again unless a
marker asks for adb or SSH (not yet checked on the phone).

Ways to create the marker files:

- `install.py --dev-access adb` or `--dev-access ssh` (`none` removes both)
- in the [installer shell](#the-installer-shell), when userdata is mounted:
  `touch /root/userdata/.force-adb`
- on the phone: `sudo touch /userdata/.force-adb`

## The installer shell

When Ubuntu Touch doesn't come up, start the phone into the Halium initramfs
instead. `boot-installer.img`, from a release or from `make-flashable.sh`, is
`boot.img` with `break=bottom` on the kernel command line. The initramfs then
stops before it starts Ubuntu Touch and gives a root shell over USB
networking. To make one from any `boot.img`:

```sh
tools/make-debug-boot.sh boot.img boot-installer.img
```

`fastboot boot` doesn't work on this phone, so flash it to `boot_a`. In the
bootloader:

```sh
fastboot flash boot_a boot-installer.img
fastboot reboot
```

The screen keeps showing the Google logo. The phone appears as a USB network
device (USB ID `18d1:d001`, "Failed to boot" from "Halium initrd"), takes
192.168.2.15 and gives the computer an address between 192.168.2.20 and
192.168.2.90 by DHCP. If the computer gets no address, see
[the install troubleshooting](install.md#the-installer-doesnt-reach-192168215).
Then:

```sh
telnet 192.168.2.15
```

macOS has no `telnet` command; install it with `brew install telnet`. The
shell is busybox ash (1.22) running as root, without a password.

What the shell finds mounted depends on userdata:

- **ext4 with `ubuntu.img`** (an installed phone): the initramfs has mounted
  the rootfs, `ubuntu.img`, at `/root` and userdata at `/root/userdata`, with
  more bind mounts under `/root`.
- **Anything else**, such as Android's encrypted userdata: nothing is mounted.
  The initramfs couldn't find a rootfs and started the same shell from its
  panic handler.

To leave the shell for the bootloader, write the one-time boot command into
`misc` and restart. `reboot bootloader` doesn't work here: the initramfs's
`reboot` commands can't pass a reason such as `bootloader`.

```sh
printf 'bootonce-bootloader' | dd of=/dev/disk/by-partlabel/misc bs=32 count=1 conv=notrunc,sync
sync
reboot -f
```

This writes only the 32-byte command field of the bootloader control block at
the start of `misc`. Holding Power and Volume Down works too. In the
bootloader, flash the normal boot image again:

```sh
fastboot flash boot_a boot.img
fastboot reboot
```

## Reading the journal

Ubuntu Touch keeps the journal on userdata, in
`/userdata/system-data/var/log/journal`. On a running phone:

```sh
sudo journalctl -b          # this boot
sudo journalctl -b -1       # the boot before
sudo journalctl -k          # kernel messages only
```

For a boot that didn't come up, start the installer shell and read the journal
with the rootfs's own `journalctl`:

```sh
chroot /root journalctl -D /userdata/system-data/var/log/journal --list-boots
chroot /root journalctl -D /userdata/system-data/var/log/journal -b <boot ID> --no-pager
```

`--list-boots` lists the boots in the journal, the newest last; pass the boot
ID of the one you want to `-b`.

In devel images the journal also keeps kernel messages
(`/etc/systemd/journald.conf.d/zz-bluejay-bringup.conf`). They include the
Android container's init log: service exits, `reboot_on_failure`, apexd and
`insmod.sh`. Release images leave kernel messages out, as Ubuntu Touch does by
default.

## Kernel panics and restarts: pstore

The kernel keeps its console log in a reserved RAM area (ramoops) that
survives a restart without power loss, such as the restart after a kernel
panic. At the next start of Ubuntu Touch, `systemd-pstore.service` moves the
previous boot's log out of `/sys/fs/pstore`:

```sh
sudo ls /var/lib/systemd/pstore/
sudo cat /var/lib/systemd/pstore/console-ramoops-0
```

This is how the Wi-Fi Aware kernel panic of build 2 was found (see the
[porting notes](porting-notes.md#wi-fi-aware_nmi0-and-a-kernel-panic)). A
power-off clears the RAM area.

## The Android side

Android 16's vendor services run in an LXC container. Ubuntu Touch's
`getprop` reads its properties, which shows whether a service runs:

```sh
getprop | grep init.svc.
getprop init.svc.vendor.hwcomposer-2-4
```

The services this port waits for are listed in the overlay's systemd drop-ins
and in `overlay/system/etc/default/lsc-wrapper.d/60-bluejay.conf`: the
composer and allocator, the sensors HAL, the vibrator and the Bluetooth HAL
(`bcmbtlinux`).

## No sound: AoC and Trusty

Sound goes through the audio processor (AoC). Its firmware loads at boot, and
the GSA authenticates it through Trusty. Check AoC, Trusty's own log and the
services that use Trusty:

```sh
head -1 /sys/devices/platform/19000000.aoc/revision
sudo timeout 3 cat /dev/trusty-log0
getprop init.svc.vendor.keymint-trusty
```

On a good boot, AoC shows `FW Revision : 0x0` and KeyMint is `running`. If
AoC is `Offline`, KeyMint is `restarting` and Trusty's log stops at
`validate_descr:1158: unexpected status 1`, Trusty refused IPC for this boot.
See [the module order](porting-notes.md#trusty-ipc-and-the-module-order).

## The installer

`install.py --verbose` prints every command it runs, on the computer (`$ ...`)
and on the phone (`phone# ...`), with their output, on stderr. Warnings and
errors also go to stderr; the rest goes to stdout. For a bug report, save both:

```sh
python3 install.py --verbose 2>&1 | tee install-log.txt
```

and attach `install-log.txt`. The
[porting notes](porting-notes.md#the-installer) explain what it does on the
phone and why.

## USB IDs

| Mode | USB ID |
|---|---|
| Installer shell (Halium initramfs) | `18d1:d001` |
| Ubuntu Touch with adb (`charging_only_adb`) | `18d1:4ee7` |
| Ubuntu Touch with USB networking and SSH (`developer_mode`) | `18d1:4ee3` |

Ubuntu Touch uses the stock Pixel 6a IDs, set in
`overlay/system/etc/default/usb-moded.d/bluejay.conf`.
