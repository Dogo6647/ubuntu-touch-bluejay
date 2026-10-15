# Ubuntu Touch for the Google Pixel 6a (bluejay)

An unofficial [Ubuntu Touch](https://ubports.com) port for the Google Pixel 6a.

> **Status: early work in progress.** Nothing has been confirmed working on
> the device yet. Don't install this on a phone you depend on: flashing it
> wipes the phone, and calls, camera and other hardware may never work.

## How it works

This is a Halium GSI port, built the same way as other UBports community ports:

| Part | Source |
|---|---|
| Firmware, vendor and vendor_dlkm | Stock Android 16 from Google, build **BP4A.251205.006**, left untouched on the phone |
| Kernel | UBports' generic GKI kernel, branch [`android14-6.1-halium`](https://gitlab.com/ubports/porting/community-ports/android12/generic/kernel-android-common/-/tree/android14-6.1-halium) |
| Android system image (in an LXC container) | Generic Halium 16 GSI built by UBports CI |
| Rootfs | Ubuntu Touch 24.04-2.x |
| Shared GKI ramdisk and rootfs overlays | UBports' [halium-gki](https://gitlab.com/ubports/porting/community-ports/android12/generic/halium-gki) port |

The stock kernel on BP4A.251205.006 is GKI `6.1.145-android14-11`. The Halium
kernel is built on the same GKI branch and keeps its ABI, so the stock vendor
modules (first-stage ones in `vendor_boot`, the rest in `vendor_dlkm`) are meant
to load unchanged. Halium 16 is based on LineageOS 23.2, and LineageOS 23.2 for
bluejay is based on the same BP4A.251205.006 build. So the Halium system image
matches the vendor partitions on the phone.

**Keep the phone on BP4A.251205.006.** A newer Android version on the phone
won't match the Halium 16 system image.

## Building

The build runs on Linux (x86_64). GitHub Actions builds every push to `main`.
See [`.github/workflows/build.yml`](.github/workflows/build.yml).

```sh
./build.sh            # kernel, boot.img and device tarball, into out/
./make-flashable.sh   # out/boot.img and out/system.img (needs sudo)
```

`build.sh` pins the versions of UBports' build tools and of halium-gki it uses.
The files in this repository's `overlay/` and `ramdisk-overlay/` (if any) are
applied on top of halium-gki's.

## Installing (development images)

You need an unlocked bootloader, the stock BP4A.251205.006 firmware, and
`fastboot` from a recent Android platform-tools.

1. Flash stock BP4A.251205.006 with Google's factory image if the phone is on
   another build.
2. In the bootloader, unlock it (**this wipes the phone**):
   `fastboot flashing unlock`
3. Flash the Ubuntu Touch rootfs to the `system` partition from fastbootd, and
   format userdata as ext4:

   ```sh
   fastboot reboot fastboot
   fastboot delete-logical-partition product_a
   fastboot flash system system.img
   fastboot format:ext4 userdata
   fastboot reboot bootloader
   ```

4. Flash the boot image and reboot:

   ```sh
   fastboot flash boot boot.img
   fastboot reboot
   ```

Development images run adbd without authorization, and SSH accepts logins
without a password. Use them only on a phone you're testing with.

To go back to Android, flash Google's factory image for BP4A.251205.006 with
`flash-all.sh`.

## Credits

This port is built on the work of the UBports and Halium communities:
the build tools, the GKI kernel and ramdisk, the Halium GSI and Ubuntu Touch
itself.

## License

Files in this repository are licensed under the GNU General Public License
v3.0 or later. See [LICENSE](LICENSE). Components fetched during the build keep
their own licenses.
