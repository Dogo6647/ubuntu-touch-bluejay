# Installing Ubuntu Touch on the Pixel 6a

This guide installs a release of this port on a Google Pixel 6a (bluejay). It
also covers upgrades, developer access, security, going back to Android and
troubleshooting.

> **Installing erases everything on the phone.** This is an alpha: calls and
> mobile data haven't been tested. Back up what you need first.

## What you need

- A Google Pixel 6a (codename bluejay). Other Pixel models don't work with
  this port.
- A USB cable that carries data.
- A computer with macOS or Linux. The installer hasn't been tested on Windows.
  Step 2 (Google's `flash-all.sh`) may need a Linux computer: see
  [flash-all.sh stops at fastbootd](#flash-allsh-stops-at-fastbootd).
- Python 3.8 or newer. The installer uses only Python's standard library.
- `fastboot` from Google's Android SDK Platform-Tools. `adb` from the same
  package is optional: with it, the installer can restart the phone from
  Android into the bootloader.
- `zstd`, to unpack `system.img.zst`.
- About 1.2 GB of disk space for the release files, and about 7 GB more if you
  need Google's factory image (the 3.5 GB download and its unzipped folder;
  you can delete the zip after unzipping).

On the phone:

- Stock Android 16, build **BP4A.251205.006**, on slot a (step 2).
- An unlocked bootloader (step 1).
- Battery charged to at least 30%.

### Installing the tools

On macOS, with [Homebrew](https://brew.sh):

```sh
brew install --cask android-platform-tools
brew install zstd
```

macOS has `python3` once the Xcode Command Line Tools are installed. Run
`python3 --version`; if they are missing, macOS offers to install them.

On a Mac laptop with Apple silicon, macOS may ask whether to allow a new USB
accessory to connect. The phone can show up as a new accessory several times:
in Android, in the bootloader, and when the installer starts on the phone.
Click Allow each time. The setting is "Allow accessories to connect" in System
Settings > Privacy & Security.

On Debian or Ubuntu:

```sh
sudo apt install python3 zstd curl unzip android-sdk-platform-tools-common
curl -LO https://dl.google.com/android/repository/platform-tools-latest-linux.zip
unzip platform-tools-latest-linux.zip
export PATH="$PWD/platform-tools:$PATH"
```

`android-sdk-platform-tools-common` installs the udev rules that let your user
access the phone over USB. Use Google's platform-tools rather than the
distribution's `fastboot` package: the factory image's `flash-all.sh` refuses
`fastboot` older than 33.0.1, and distributions can ship older versions. The
`export` line only lasts for that terminal; add it to your shell profile to
keep it.

## Before you start

Unlocking (step 1) erases the phone, so check first that it can run this
release:

1. In Android, open Settings > About phone and note the **Build number**.
2. If it is `BP4A.251205.006`, the phone is ready for step 1. If it is newer,
   step 2 installs that older build again, which anti-rollback may refuse:

> **Check before you downgrade.** BP4A.251205.006 is a December 2025 build.
> If the phone runs a newer build, this step installs an older one. The
> Pixel 6a has had bootloader anti-rollback since Android 13: some updates
> raise the bootloader's anti-rollback version, and after that, flashing an
> older bootloader fails. Google's
> [factory images page](https://developers.google.com/android/images) warns
> about these updates; for the Pixel 6a it names the May 2025 update. If an
> update after December 2025 raised the version, the phone can't go back to
> BP4A.251205.006 and can't run this release. Check that page for such a
> warning before you unlock. The steps below flash the bootloader first, so you
> can stop before anything else changes if it is refused.

## Step 1: Unlock the bootloader

Skip this step if the bootloader is already unlocked (in the bootloader,
`fastboot getvar unlocked` prints `unlocked: yes`).

1. In Android, open Settings > About phone and tap **Build number** seven times
   to turn on the developer options.
2. In Settings > System > Developer options, turn on **OEM unlocking**. Also
   turn on **USB debugging**, so that `adb` can restart the phone.

   If OEM unlocking is greyed out, connect the phone to the internet and try
   again later. Phones sold by some carriers can't be unlocked.
3. Connect the phone and restart it into the bootloader:

   ```sh
   adb reboot bootloader
   ```

   Allow the computer on the phone's screen if it asks. Without adb: turn the
   phone off, then hold Power and Volume Down until the bootloader (fastboot
   mode) appears.
4. Unlock the bootloader. **This erases all data on the phone.**

   ```sh
   fastboot flashing unlock
   ```

   On the phone, choose to unlock with the volume keys and confirm with Power.

From now on the phone shows a warning about the unlocked bootloader every time
it starts. That's normal.

## Step 2: Put stock Android BP4A.251205.006 on the phone

The Ubuntu Touch images only work with the vendor partitions of this exact
Android build, and only on slot a. In Android, Settings > About phone > Build
number shows the build. In the bootloader, `fastboot getvar current-slot`
shows the slot. If the build is `BP4A.251205.006` and the slot is `a`, go to
step 3. Otherwise, including the same build on slot b, flash Google's factory
image for BP4A.251205.006 as below. It needs the unlocked bootloader from
step 1 and erases the phone.

The installer checks this too: the bootloader version must be
`bluejay-16.4-14097577` and the slot must be `a`. Read
[Before you start](#before-you-start) first if the phone runs a newer build.

1. Download the factory image and check its SHA-256:

   ```sh
   curl -LO https://dl.google.com/dl/android/aosp/bluejay-bp4a.251205.006-factory-203dc556.zip
   shasum -a 256 bluejay-bp4a.251205.006-factory-203dc556.zip   # macOS
   sha256sum bluejay-bp4a.251205.006-factory-203dc556.zip       # Linux
   ```

   The checksum must be
   `203dc5566d59a1851d89ca596ae197286cc14c68c99f171c713b04aa3faefa3a`.
2. Unzip it:

   ```sh
   unzip bluejay-bp4a.251205.006-factory-203dc556.zip
   cd bluejay-bp4a.251205.006
   ```

3. Put the phone in the bootloader (see step 1) and check which slot is
   active:

   ```sh
   fastboot getvar current-slot
   ```

4. Flash the bootloader of BP4A.251205.006 to slot a:

   ```sh
   fastboot --slot=a flash bootloader bootloader-bluejay-bluejay-16.4-14097577.img
   ```

   If it prints `FAILED`, stop and read the message. If the phone refuses the
   bootloader, for example because of anti-rollback, it can't run this
   release. A wrong folder or file name, or a USB error, also prints `FAILED`.
5. If the slot was `b`, switch to slot a now:

   ```sh
   fastboot --set-active=a
   ```

   Don't switch before step 4. Slot a may still hold an older bootloader, and
   switching to it can leave the phone unable to start.
6. Restart the bootloader and check it:

   ```sh
   fastboot reboot bootloader
   fastboot getvar version-bootloader
   fastboot getvar current-slot
   ```

   They must print `bluejay-16.4-14097577` and `a`.
7. Flash the same bootloader to slot b too, so that a fallback to slot b
   doesn't land on an older bootloader. Google's factory images page asks for
   this after the anti-rollback updates (not tried on this port's phone yet):

   ```sh
   fastboot --slot=b flash bootloader bootloader-bluejay-bluejay-16.4-14097577.img
   ```

8. Flash the factory image:

   ```sh
   ./flash-all.sh
   ```

   It flashes the bootloader again, then the modem firmware and Android. It
   erases userdata and starts Android. Keep the phone connected until it has
   finished.

   `flash-all.sh` doesn't stop on errors. If a step prints `FAILED`, don't
   install Ubuntu Touch yet: fix the problem and run `flash-all.sh` again. If
   it stops at the step that restarts into fastbootd, see
   [Troubleshooting](#flash-allsh-stops-at-fastbootd).

You don't need to set up Android afterwards: the installer erases it again.

## Step 3: Download the release

Open the [Releases page](https://github.com/logicalangel/ubuntu-touch-bluejay/releases)
and download the six files of the release into one new, empty folder. You
don't need the "Source code" archives that GitHub adds to every release.

| File | What it is |
|---|---|
| `boot.img` | The Ubuntu Touch boot image (kernel and Halium initramfs), flashed to `boot_a` |
| `boot-installer.img` | A copy of `boot.img` that stops in the initramfs; the installer copies the system image from there |
| `system.img.zst` | The Ubuntu Touch system, compressed; it becomes `/userdata/ubuntu.img` on the phone |
| `install.py` | The installer |
| `build-info.txt` | What the images were built from (see [building](building.md#build-infotxt)); the installer doesn't need it |
| `SHA256SUMS` | Checksums of the other files; the installer checks them before it changes anything |

With the [GitHub CLI](https://cli.github.com), for example for v0.1.0 (this
downloads the release files without the source code archives):

```sh
mkdir ut-bluejay
cd ut-bluejay
gh release download v0.1.0 --repo logicalangel/ubuntu-touch-bluejay
```

## Step 4: Run the installer

1. Put the phone in the bootloader: turn it off, then hold Power and Volume
   Down until the bootloader (fastboot mode) appears. If the phone runs
   Android with USB debugging on, the installer can also restart it with adb.
   It offers this only for a device that reports itself as a Pixel 6a
   (`bluejay`).
2. Connect it to the computer, preferably directly rather than through a hub.
   On a Mac laptop with Apple silicon, allow the accessory if macOS asks (see
   [Installing the tools](#installing-the-tools)).
3. In the folder with the release files, run:

   ```sh
   python3 install.py
   ```

The installer:

1. checks the files against `SHA256SUMS`, checks that `boot.img` and
   `boot-installer.img` aren't swapped, and checks that `fastboot` (and
   `zstd`) are installed,
2. waits up to 3 minutes for one phone in fastboot mode and checks it: a
   Pixel 6a, bootloader unlocked, slot a, bootloader
   `bluejay-16.4-14097577`, battery at least 30%,
3. shows what it will do and asks you to confirm (for a fresh install, by
   typing `yes`),
4. flashes `boot-installer.img` to `boot_a` and restarts the phone. The phone
   shows the Google logo, and the computer gets a new USB network connection
   to it,
5. checks that the installer it reaches runs on the same phone (by its serial
   number) and has the tools it needs, before it changes userdata,
6. formats userdata as ext4 (not with `--keep-data`) and copies the system
   image to `/userdata/ubuntu.img` (about 2 minutes), then checks the copy on
   the phone (about a minute),
7. sets developer access, if you asked for it with `--dev-access`,
8. restarts the phone into the bootloader, flashes `boot.img` to `boot_a` and
   starts Ubuntu Touch.

The whole install takes about 5 minutes. Don't disconnect the phone until the
installer says `Done`.

The installer only writes `boot_a`, userdata and the one-time boot command in
the `misc` partition. It never flashes the bootloader, the modem firmware,
vbmeta or the `super` partition with stock Android's system.

### Options

```text
python3 install.py [-h] [--keep-data] [--dev-access {none,adb,ssh}] [--yes] [--images DIR] [--verbose]
```

| Option | Effect |
|---|---|
| `--keep-data` | Keep userdata (your files, apps and settings) and replace only `/userdata/ubuntu.img`. Needs an existing install of this port. See [Upgrading](#upgrading). |
| `--dev-access adb` | Turn on adb, from the moment the user interface starts. |
| `--dev-access ssh` | Turn on USB networking with SSH. Until you set a passcode, SSH accepts `phablet` without a password. |
| `--dev-access none` | Turn adb and SSH off. |
| `--yes` | Don't ask for confirmation. |
| `--images DIR` | Use the image files in `DIR` instead of the installer's own folder. `DIR` needs `boot.img`, `boot-installer.img`, and `system.img.zst` or `system.img`. |
| `--verbose` | Show every command run on the computer and on the phone. |

Without `--dev-access`, developer access stays as it is: off on a new install.
See [Developer access](#developer-access).

The installer exits with 0 when it's done, 1 on an error or when you cancel,
2 for wrong arguments and 130 when you press Ctrl-C.

## Step 5: First start

The first start takes about a minute: the unlocked-bootloader warning and the
Google logo come first. Then the welcome wizard sets up the language, Wi-Fi and
a passcode.

Set a passcode. Developer Mode needs one, and SSH (if you turn it on) accepts
the passcode as the password, or no password at all when there's none.

Never lock the bootloader (`fastboot flashing lock`) while Ubuntu Touch is
installed: the bootloader would refuse to start it.

## Security

- The bootloader stays unlocked, and userdata is plain ext4 without
  encryption. The passcode locks the screen; it doesn't encrypt your files.
- So anyone who has the phone and a USB cable can read and change everything
  on it. They can start the bootloader with Power and Volume Down, flash
  `boot-installer.img` (or any boot image), and get a root shell over USB
  without a password. The Halium initramfs also offers that shell when it
  can't start Ubuntu Touch.
- With `--dev-access ssh`, SSH accepts `phablet` without a password, and sudo
  works, until you set a passcode.
- Devel images, the variant CI builds for every push, let anyone with a cable
  in: adb without authorization once adb is on (Developer Mode or
  `.force-adb`), and SSH during each boot until lightdm starts. Install
  releases on a phone you carry.

## Developer access

Release images keep adb and SSH off unless you turn them on. When adb is on,
it asks you to allow each computer on the phone's screen.

- **Developer Mode** in System Settings > About turns on adb. It needs a
  passcode.
- **`--dev-access adb`** creates `/userdata/.force-adb`. The phone switches USB
  to adb once the user interface has started.
- **`--dev-access ssh`** creates `/userdata/.force-ssh`. The phone keeps USB
  networking and an SSH server on; the computer gets an address by DHCP. Log
  in with the passcode:

  ```sh
  ssh -p 8022 phablet@10.15.19.82
  ```

  Until you set a passcode, SSH accepts `phablet` without a password. The
  installer warns about this when it's done.
- **`--dev-access none`** removes both files.

Every `--dev-access` value also deletes the USB mode that usb-moded saved on
userdata, `/userdata/system-data/var/lib/usb-moded/usb-moded.ini`. usb-moded
keeps using a saved mode after the marker file is gone, so without this,
`none` wouldn't turn adb or SSH off. The phone then starts in the image's
default USB mode, charging only, unless a marker file asks for adb or SSH (not
yet checked on the phone).
This may also turn off Developer Mode if you had turned it on in System
Settings; turn it on again there.

Without `--dev-access`, the installer changes neither the marker files nor the
saved USB mode. With `--keep-data`, adb or SSH then stays on if it was on.

If both files exist, `.force-adb` wins. To create one later without the
installer, open the Terminal app on the phone, run for example this command
(it asks for the passcode), and restart the phone:

```sh
sudo touch /userdata/.force-adb
```

## Upgrading

To install a new release and keep your files, apps and settings:

1. Download the new release into a new, empty folder (step 3).
2. Put the phone in the bootloader. From Ubuntu Touch, `adb reboot bootloader`
   doesn't work: turn the phone off, then hold Power and Volume Down.
3. In the new folder, run:

   ```sh
   python3 install.py --keep-data
   ```

The installer replaces only `/userdata/ubuntu.img`. It writes the new image
next to the old one first, so userdata needs free space for a second copy:
the image size (5 GiB) plus 64 MiB. If a release needs a fresh install, its
release notes say so.

Developer access stays as it was. When you move from a devel build to a
release, add `--dev-access none` (or `--dev-access adb`) to turn SSH off.

## Going back to Android

1. Start the bootloader. From Ubuntu Touch, `adb reboot bootloader` doesn't
   work: hold Power and choose Power off, then hold Power and Volume Down
   until the bootloader (fastboot mode) appears.
2. Flash Google's factory image as in
   [step 2](#step-2-put-stock-android-bp4a251205006-on-the-phone): download
   and check it, flash its bootloader to slot a, and run `./flash-all.sh`.
   Ubuntu Touch runs from slot a, so the slot is already right. This restores
   the boot image and erases userdata, and with it Ubuntu Touch and all its
   data.
3. `flash-all.sh` needs fastbootd, which doesn't answer on some Macs. If it
   hangs there, see [Troubleshooting](#flash-allsh-stops-at-fastbootd).

If the installer stopped before it formatted userdata, Android and its data
are still there: see [The installer stopped halfway](#the-installer-stopped-halfway).

If you also want to lock the bootloader again, do it only after Android has
started successfully from the factory image.

## Troubleshooting

When the installer stops with an error, it says what state the phone is in and
how to continue. The sections below explain the common cases. To report a
problem, run the installer again with `--verbose`, save all of its output, and
attach `install-log.txt` to an
[issue](https://github.com/logicalangel/ubuntu-touch-bluejay/issues):

```sh
python3 install.py --verbose 2>&1 | tee install-log.txt
```

Add your other options (such as `--keep-data`) to that command. The questions
still work with the output going to `tee`.

### The installer doesn't find the phone

- The phone must be in the bootloader (fastboot mode), not in Android or
  Ubuntu Touch. Check with `fastboot devices`.
- Try another cable or USB port. Some cables only charge.
- On a Mac laptop with Apple silicon, check that you allowed the accessory
  (see [Installing the tools](#installing-the-tools)).
- On Linux, `no permissions` means your user may not access the phone. Install
  the udev rules (`android-sdk-platform-tools-common` on Debian and Ubuntu),
  then unplug and plug in the phone again.
- Only one phone may be in fastboot mode at a time.

### The installer refuses the phone

- **Bootloader version**: the phone doesn't run stock BP4A.251205.006. Flash
  the factory image (step 2).
- **Locked bootloader**: unlock it (step 1).
- **Slot b**: don't just switch slots. Slot a may hold an older bootloader,
  and switching to it can leave the phone unable to start. Follow step 2: it
  flashes the BP4A.251205.006 bootloader to slot a before it switches.
- **Battery**: charge the phone to at least 30%.
- **fastbootd**: the phone is in fastbootd (userspace fastboot), not in the
  bootloader. Run `fastboot reboot bootloader`, or hold Power and Volume Down.

A different modem firmware version only gives a warning. Calls and mobile data
may not work with it.

### The installer doesn't reach 192.168.2.15

After it flashes `boot-installer.img`, the installer waits up to 2 minutes for
the phone's shell at 192.168.2.15. The phone shows the Google logo and appears
on the computer as a USB network device. Its USB name is "Failed to boot" from
"Halium initrd"; that's normal for the installer. The phone gives the computer
an address between 192.168.2.20 and 192.168.2.90 by DHCP.

Check that the computer has such an address:

- **macOS**: System Settings > Network shows the new connection. If it has a
  self-assigned 169.254.x.x address, set it to a manual address,
  192.168.2.20, subnet mask 255.255.255.0. On macOS 15 and newer, your
  terminal app may also need permission for the local network: System
  Settings > Privacy & Security > Local Network. On a Mac laptop with Apple
  silicon, macOS may first ask to allow the new USB accessory (see
  [Installing the tools](#installing-the-tools)).
- **Linux**: `ip addr` shows a new interface (for example `usb0` or
  `enx...`). If NetworkManager doesn't connect it, run `nmcli device` and
  `nmcli device connect <interface>`. Without DHCP, set an address by hand:

  ```sh
  sudo ip addr add 192.168.2.20/24 dev <interface>
  sudo ip link set <interface> up
  ```

- Some home networks also use 192.168.2.x, and VPNs can take over the route.
  Disconnect from Wi-Fi and VPNs during the install.

`ping 192.168.2.15` should answer. Then put the phone back in the bootloader
(see the next section) and run the installer again.

The installer also checks that the shell at 192.168.2.15 is its installer and
runs on the phone it checked in fastboot mode. If it says otherwise, connect
only the one phone and leave networks that use 192.168.2.x.

### The installer stopped halfway

The installer never changes the bootloader, so there's always a way back.
Depending on how far the installer got:

- **Before "Starting the installer on the phone"**: nothing on the phone has
  changed. Fix the problem and run the installer again.
- **After that**: `boot_a` holds the installer, so the phone starts to the
  Google logo and stays there. Hold Power and Volume Down until the
  bootloader (fastboot mode) appears, and run the installer again. If the
  installer had already formatted userdata, run it without `--keep-data`.
- **Before it formatted userdata** (the installer says "Userdata wasn't
  touched"): if the phone ran Android, its data is still there. To go back to
  Android with that data, flash the `boot.img` of Google's factory image
  (step 2) to `boot_a`. It's in the image zip inside the factory image. Work
  in the factory image's folder: the release's `boot.img` has the same name.

  ```sh
  cd bluejay-bp4a.251205.006
  unzip image-bluejay-bp4a.251205.006.zip boot.img
  fastboot flash boot_a boot.img
  fastboot reboot
  ```

  This needs only the bootloader, not fastbootd.
- **"The system image is installed; only the boot image is missing"**: in the
  bootloader, flash the boot image yourself, or run the installer again with
  `--keep-data`:

  ```sh
  fastboot flash boot_a boot.img
  fastboot reboot
  ```

You can always go back to Android with the factory image (see
[Going back to Android](#going-back-to-android)).

Ctrl-C stops the installer. It then prints the same advice.

### The copy on the phone doesn't match

The system image arrived damaged. Put the phone in the bootloader and run the
installer again. If it happens again, try another cable or USB port.

### Not enough space on userdata

`--keep-data` needs the image size (5 GiB) plus 64 MiB free on userdata.
Delete files on the phone, or do a fresh install without `--keep-data`, which
erases everything.

### flash-all.sh stops at fastbootd

The factory image's flash plan (`fastboot-info.txt` in its image zip) restarts
the phone into fastbootd to write the Android system partitions. fastbootd
doesn't answer on some Macs. If `flash-all.sh` hangs at that step, hold Power
and Volume Down to get back to the bootloader and run `flash-all.sh` from a
Linux computer. There's no tested way yet to restore Android from a Mac on
which fastbootd doesn't answer.

### The phone doesn't start Ubuntu Touch

If the phone keeps showing the Google logo or restarts after the install, see
[debugging](debugging.md) for how to read the logs of a boot that didn't come
up, and open an issue.
