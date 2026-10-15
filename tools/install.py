#!/usr/bin/env python3
"""Install Ubuntu Touch on a Google Pixel 6a (bluejay).

Put boot.img, boot-installer.img, system.img.zst (or system.img) and, if you
have it, SHA256SUMS in one folder with this script, put the phone in its
bootloader and run python3 install.py. It:

  1. checks the phone: a Pixel 6a with an unlocked bootloader, slot a, on the
     stock BP4A.251205.006 bootloader,
  2. flashes boot-installer.img to boot_a: the Ubuntu Touch boot image with
     "break=bottom", which stops in the Halium initramfs and offers a root
     shell over USB networking (telnet to 192.168.2.15),
  3. in that shell formats userdata as ext4 (unless --keep-data) and copies
     the system image to /userdata/ubuntu.img over TCP, checking its SHA-256,
  4. restarts into the bootloader (through the boot command in misc) and
     flashes boot.img to boot_a.

It needs no fastbootd, which doesn't answer some Macs, and doesn't touch the
stock super partition: it only writes boot_a, userdata and the boot command
field in misc. Needs Python 3.8 or newer (standard library only), fastboot
and, for system.img.zst, zstd.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

# The phone and the stock Android build this port needs
PRODUCT = "bluejay"
STOCK_BUILD = "BP4A.251205.006"
STOCK_BOOTLOADER = "bluejay-16.4-14097577"
STOCK_BASEBAND = "g5123b-145971-251030-B-14356419"
FACTORY_IMAGE_URL = ("https://dl.google.com/dl/android/aosp/"
                     "bluejay-bp4a.251205.006-factory-203dc556.zip")
FACTORY_IMAGE_SHA256 = "203dc5566d59a1851d89ca596ae197286cc14c68c99f171c713b04aa3faefa3a"
MIN_BATTERY = 30

DOCS_URL = "https://github.com/logicalangel/ubuntu-touch-bluejay/blob/main/docs/install.md"
PLATFORM_TOOLS_URL = "https://developer.android.com/tools/releases/platform-tools"

# The Halium initramfs reads the slot only from /proc/cmdline and falls back
# to _a, so the port lives on slot a. boot_a is the only partition flashed.
BOOT_PARTITION = "boot_a"
BOOT_PARTITION_SIZE = 64 << 20
INSTALLER_CMDLINE = "break=bottom"

# The installer's shell: the Halium initramfs panic handler starts CDC-NCM
# USB networking, udhcpd and busybox telnetd with a root shell
PHONE_IP = "192.168.2.15"
TELNET_PORT = 23
STREAM_PORT = 5555
USERDATA_DEV = "/dev/disk/by-partlabel/userdata"
MISC_DEV = "/dev/disk/by-partlabel/misc"
MOUNT_DIR = "/mnt/userdata"
ROOTFS_FILE = "ubuntu.img"
NC_STATUS = "/run/install-nc.status"
DEV_MARKERS = {"adb": ".force-adb", "ssh": ".force-ssh"}
# usb-moded saves the last USB mode it was told to use (e.g. by the markers)
# here, on userdata, and keeps using it after the marker is gone
USB_MODED_SAVED = "system-data/var/lib/usb-moded/usb-moded.ini"

# Where the initramfs mounts userdata and the images on it: /tmpmnt and
# /halium-system are moved under /root in a normal boot, and stay where they
# are when it stops early (userdata without ubuntu.img)
INITRAMFS_MOUNT_ROOTS = ("/root", "/tmpmnt", "/halium-system", "/android-rootfs",
                         "/android-system", MOUNT_DIR)

# Seconds
FIND_PHONE_WAIT = 180
INSTALLER_WAIT = 120
BOOTLOADER_WAIT = 60

# busybox ash's line editor keeps at most 1024 bytes of a command line
MAX_COMMAND_LINE = 900

_verbose = False


class InstallError(Exception):
    """A failure, with a message for the user."""


class Cancelled(Exception):
    """The user answered no."""


def info(message: str = "") -> None:
    print(message, flush=True)


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr, flush=True)


def debug(message: str) -> None:
    if _verbose:
        print(f"    {message}", file=sys.stderr, flush=True)


def gib(nbytes: int) -> str:
    return f"{nbytes / (1 << 30):.2f} GiB"


def ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise InstallError("There's no terminal to ask for confirmation. Run again with --yes.")
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


# Release files ##############################################################

class Images(NamedTuple):
    folder: Path
    boot: Path
    installer: Path
    system: Path  # system.img or system.img.zst


_SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


def parse_sha256sums(text: str) -> Dict[str, str]:
    """Parses sha256sum output ("<hash>  <file>"). Only plain file names are allowed."""
    sums = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SUMS_LINE.match(line)
        if not match:
            raise InstallError(f"SHA256SUMS line {number} isn't '<sha256>  <file>': {line!r}")
        name = match.group(2)
        if name in (".", "..") or "/" in name or "\\" in name:
            raise InstallError(f"SHA256SUMS line {number} names a file outside its folder: {name!r}")
        sums[name] = match.group(1).lower()
    return sums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def verify_sha256sums(folder: Path) -> Optional[Dict[str, str]]:
    """Checks every file SHA256SUMS lists that is in the folder.

    Returns the listing, or None without SHA256SUMS.
    """
    path = folder / "SHA256SUMS"
    if not path.is_file():
        return None
    sums = parse_sha256sums(path.read_text(encoding="utf-8", errors="replace"))
    bad = []
    for name, expected in sorted(sums.items()):
        if not (folder / name).is_file():
            debug(f"{name}: listed in SHA256SUMS, not in the folder")
            continue
        if sha256_file(folder / name) == expected:
            debug(f"{name}: OK")
        else:
            bad.append(name)
    if bad:
        raise InstallError("These files don't match SHA256SUMS: " + ", ".join(bad) +
                           ".\nThe download is probably broken or incomplete. Download them again.")
    return sums


def find_images(folder: Path) -> Images:
    if not folder.is_dir():
        raise InstallError(f"{folder} isn't a folder.")
    system = folder / "system.img.zst"
    if not system.is_file():
        system = folder / "system.img"
    images = Images(folder, folder / "boot.img", folder / "boot-installer.img", system)
    missing = [p.name for p in (images.boot, images.installer) if not p.is_file()]
    if not images.system.is_file():
        missing.append("system.img.zst (or system.img)")
    if missing:
        raise InstallError(f"Missing in {folder}: {', '.join(missing)}.\n"
                           "Download all files of a release into one folder and run install.py "
                           "from there, or point --images at the folder with the images.")
    return images


BOOT_MAGIC = b"ANDROID!"


def boot_image_cmdline(header: bytes) -> str:
    """The kernel command line in an Android boot image header (versions 0 to 4)."""
    if header[:8] != BOOT_MAGIC or len(header) < 1632:
        raise ValueError("not an Android boot image")
    version = struct.unpack_from("<I", header, 40)[0]
    if version >= 3:
        raw = header[44:44 + 1536]
    else:
        # cmdline (512 bytes) after the name, extra_cmdline (1024) after the id
        raw = header[64:64 + 512].split(b"\0", 1)[0] + header[608:608 + 1024]
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def check_boot_images(images: Images) -> None:
    for path, is_installer in ((images.boot, False), (images.installer, True)):
        size = path.stat().st_size
        if size > BOOT_PARTITION_SIZE:
            raise InstallError(f"{path.name} is {size} bytes, more than {BOOT_PARTITION} holds.")
        with open(path, "rb") as f:
            header = f.read(4096)
        try:
            cmdline = boot_image_cmdline(header)
        except ValueError:
            raise InstallError(f"{path.name} isn't an Android boot image.") from None
        # Swapped files would leave the phone in the installer, or format
        # userdata and boot nothing
        if is_installer and INSTALLER_CMDLINE not in cmdline.split():
            raise InstallError(f"{path.name} doesn't stop in the initramfs (no {INSTALLER_CMDLINE} "
                               "in its kernel command line). Is it a renamed boot.img?")
        if not is_installer and "break=" in cmdline:
            raise InstallError(f"{path.name} stops in the initramfs ({cmdline}). "
                               "Is it a renamed boot-installer.img?")


# System image ###############################################################

SPARSE_MAGIC = 0xED26FF3A
SPARSE_HEADER = struct.Struct("<IHHHHIIII")
CHUNK_HEADER = struct.Struct("<HHII")
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
EXT4_MAGIC_OFFSET = 0x438
EXT4_MAGIC = b"\x53\xef"
PIECE = 1 << 20
ZEROS = bytes(PIECE)


class ImageFormatError(InstallError):
    pass


class SparseHeader(NamedTuple):
    file_header_size: int
    chunk_header_size: int
    block_size: int
    total_blocks: int
    total_chunks: int

    @property
    def size(self) -> int:
        return self.block_size * self.total_blocks


def read_exact(reader, size: int) -> bytes:
    """Reads size bytes, or fewer at the end of the stream (pipes return short reads)."""
    pieces = []
    while size > 0:
        piece = reader.read(size)
        if not piece:
            break
        pieces.append(piece)
        size -= len(piece)
    return b"".join(pieces)


def _read_full(reader, size: int, what: str) -> bytes:
    data = read_exact(reader, size)
    if len(data) != size:
        raise ImageFormatError(f"The system image is truncated ({what}).")
    return data


def _skip(reader, size: int, what: str) -> None:
    while size > 0:
        size -= len(_read_full(reader, min(size, PIECE), what))


def parse_sparse_header(head: bytes) -> Optional[SparseHeader]:
    """The header of an Android sparse image, or None for anything else."""
    if len(head) < SPARSE_HEADER.size:
        return None
    (magic, major, _minor, file_hdr_sz, chunk_hdr_sz, blk_sz, total_blks, total_chunks,
     _checksum) = SPARSE_HEADER.unpack_from(head)
    if magic != SPARSE_MAGIC:
        return None
    if major != 1:
        raise ImageFormatError(f"Unsupported Android sparse image version {major}.")
    if (file_hdr_sz < SPARSE_HEADER.size or chunk_hdr_sz < CHUNK_HEADER.size
            or blk_sz == 0 or blk_sz % 4):
        raise ImageFormatError("The system image has a broken Android sparse header.")
    return SparseHeader(file_hdr_sz, chunk_hdr_sz, blk_sz, total_blks, total_chunks)


def _repeat(pattern: bytes, size: int) -> Iterator[bytes]:
    while size > 0:
        n = min(size, len(pattern))
        yield pattern[:n]
        size -= n


def expand_sparse(reader, header: SparseHeader) -> Iterator[bytes]:
    """Yields the raw image of an Android sparse image (format v1).

    The reader is positioned right after the 28-byte header and is only read
    forward, so it can be a pipe.
    """
    _skip(reader, header.file_header_size - SPARSE_HEADER.size, "file header")
    blocks = 0
    for index in range(header.total_chunks):
        what = f"chunk {index}"
        ctype, _, nblocks, total_size = CHUNK_HEADER.unpack(
            _read_full(reader, CHUNK_HEADER.size, what))
        _skip(reader, header.chunk_header_size - CHUNK_HEADER.size, what)
        data_size = total_size - header.chunk_header_size
        nbytes = nblocks * header.block_size
        if ctype == CHUNK_RAW:
            if data_size != nbytes:
                raise ImageFormatError(f"The system image is broken ({what}: bad raw size).")
            remaining = nbytes
            while remaining > 0:
                piece = _read_full(reader, min(remaining, PIECE), what)
                remaining -= len(piece)
                yield piece
        elif ctype == CHUNK_FILL:
            if data_size != 4:
                raise ImageFormatError(f"The system image is broken ({what}: bad fill size).")
            yield from _repeat(_read_full(reader, 4, what) * (PIECE // 4), nbytes)
        elif ctype == CHUNK_DONT_CARE:
            if data_size != 0:
                raise ImageFormatError(f"The system image is broken ({what}: bad skip size).")
            yield from _repeat(ZEROS, nbytes)
        elif ctype == CHUNK_CRC32:
            # A checksum of the data so far; the SHA-256 checks cover it
            _skip(reader, data_size, what)
            continue
        else:
            raise ImageFormatError(f"The system image is broken ({what}: unknown type {ctype:#x}).")
        blocks += nblocks
        if blocks > header.total_blocks:
            raise ImageFormatError("The system image has more blocks than its header says.")
    if blocks != header.total_blocks:
        raise ImageFormatError(f"The system image has {blocks} blocks, its header says "
                               f"{header.total_blocks}.")


def iter_image(reader) -> Iterator[bytes]:
    """Yields the raw bytes of an image: expands an Android sparse image and
    passes anything else through."""
    head = read_exact(reader, SPARSE_HEADER.size)
    header = parse_sparse_header(head)
    if header is not None:
        yield from expand_sparse(reader, header)
        return
    if head:
        yield head
    for piece in iter(lambda: reader.read(PIECE), b""):
        yield piece


class _ImageFile:
    """The bytes of system.img, or of system.img.zst decompressed by zstd."""

    def __init__(self, path: Path, zstd: Optional[str]):
        self._path = path
        self._proc = None
        if zstd:
            cmd = [zstd, "-d", "-c", "-q", "--", str(path)]
            debug("$ " + shlex.join(cmd))
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
            self._file = self._proc.stdout
        else:
            self._file = open(path, "rb")

    def read(self, size: int) -> bytes:
        return self._file.read(size)

    def finish(self) -> None:
        """Checks that the decompression succeeded. Call after reading everything."""
        if self._proc is None:
            return
        for _ in iter(lambda: self._file.read(PIECE), b""):
            pass
        status = self._proc.wait()
        if status != 0:
            raise ImageFormatError(f"zstd couldn't decompress {self._path.name} (exit status {status}).")

    def close(self) -> None:
        # Stop zstd before closing its pipe, so it doesn't report a broken pipe
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        self._file.close()

    def __enter__(self) -> "_ImageFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class SystemImage:
    """The raw ext4 image that becomes /userdata/ubuntu.img.

    Reads system.img (Android sparse or raw) or system.img.zst, which is
    decompressed on the fly: no temporary copy on the computer.
    """

    def __init__(self, path: Path, zstd: Optional[str] = None):
        self.path = path
        self.zstd = zstd if path.suffix == ".zst" else None
        self.sparse = False
        self.size = 0

    def inspect(self) -> None:
        """Reads the header: the image's format and expanded size."""
        with _ImageFile(self.path, self.zstd) as f:
            head = read_exact(f, 4096)
        header = parse_sparse_header(head)
        if header is not None:
            self.sparse = True
            self.size = header.size
            return
        if head[EXT4_MAGIC_OFFSET:EXT4_MAGIC_OFFSET + 2] != EXT4_MAGIC:
            raise ImageFormatError(f"{self.path.name} is neither an Android sparse image "
                                   "nor an ext4 image.")
        if self.zstd is None:
            self.size = self.path.stat().st_size
        else:
            # The zstd command doesn't tell the size up front
            self.size = sum(len(piece) for piece in self.chunks())

    def chunks(self) -> Iterator[bytes]:
        with _ImageFile(self.path, self.zstd) as f:
            yield from iter_image(f)
            f.finish()

    def describe(self) -> str:
        kind = "Android sparse image" if self.sparse else "raw ext4 image"
        return f"{self.path.name} ({kind}, {gib(self.size)} on the phone)"


class Progress:
    """Prints a line every 5%, with the speed."""

    def __init__(self, total: int, clock=time.monotonic, out=None):
        self.total = total
        self._clock = clock
        self._out = out or info
        self._start = clock()
        self._next = 5

    def update(self, done: int) -> None:
        percent = done * 100 // self.total if self.total else 100
        if percent >= self._next:
            self._next = (percent // 5 + 1) * 5
            elapsed = max(self._clock() - self._start, 1e-6)
            self._out(f"  {percent:3d}%  {gib(done)} of {gib(self.total)}  "
                      f"{done / elapsed / 1e6:.1f} MB/s")


def connect_with_retry(host: str, port: int, timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return socket.create_connection((host, port), timeout=3)
        except OSError as e:
            if time.monotonic() >= deadline:
                raise InstallError(f"Couldn't connect to {host}:{port}: {e}") from None
            time.sleep(0.5)


def send_image(chunks: Iterable[bytes], host: str, port: int, total: int) -> Tuple[str, int]:
    """Streams the image to host:port. Returns its SHA-256 and size."""
    digest = hashlib.sha256()
    sent = 0
    progress = Progress(total)
    with connect_with_retry(host, port, 20) as sock:
        sock.settimeout(120)
        for chunk in chunks:
            sock.sendall(chunk)
            digest.update(chunk)
            sent += len(chunk)
            progress.update(sent)
        sock.shutdown(socket.SHUT_WR)
        try:
            while sock.recv(65536):
                pass
        except OSError:
            pass
    return digest.hexdigest(), sent


# Host tools: fastboot and adb ###############################################

class HostTools(NamedTuple):
    fastboot: str
    adb: Optional[str]
    zstd: Optional[str]


def check_host_tools(need_zstd: bool) -> HostTools:
    fastboot = shutil.which("fastboot")
    if not fastboot:
        raise InstallError(
            "fastboot isn't installed (or not in PATH). Install Android's platform-tools:\n"
            f"  {PLATFORM_TOOLS_URL}\n"
            "  (macOS: brew install --cask android-platform-tools; Linux: Google's platform-tools "
            f"zip, see {DOCS_URL}#installing-the-tools)")
    zstd = shutil.which("zstd") if need_zstd else None
    if need_zstd and not zstd:
        raise InstallError("system.img.zst needs the zstd command. Install zstd "
                           "(macOS: brew install zstd; Debian/Ubuntu: apt install zstd), or "
                           "decompress it yourself with: zstd -d system.img.zst")
    return HostTools(fastboot, shutil.which("adb"), zstd)


def _run_tool(cmd: List[str], timeout: float) -> subprocess.CompletedProcess:
    debug("$ " + shlex.join(cmd))
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        raise InstallError(f"{Path(cmd[0]).name} didn't finish in {timeout:.0f} s: "
                           + " ".join(cmd[1:])) from None
    except OSError as e:
        raise InstallError(f"Couldn't run {cmd[0]}: {e}") from None
    for line in (result.stdout + result.stderr).splitlines():
        debug(f"  {line}")
    return result


def parse_getvar(output: str, name: str) -> Optional[str]:
    """The value in "fastboot getvar" output ("name: value", optionally after
    "(bootloader) "), or None if the bootloader doesn't know the variable."""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("(bootloader)"):
            line = line[len("(bootloader)"):].strip()
        if line.startswith(name + ":"):
            return line[len(name) + 1:].strip()
    return None


def parse_device_list(output: str) -> List[Tuple[str, str]]:
    """(serial, state) pairs from "fastboot devices" or "adb devices"."""
    devices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or line.startswith(("*", "List of devices")):
            continue
        if "no permissions" in line:
            raise InstallError(
                "The phone is connected but this user may not access it over USB (\"no "
                "permissions\"). On Linux, install udev rules for Android devices (for example "
                "the android-sdk-platform-tools-common package) and plug the phone in again.")
        devices.append((fields[0], fields[1]))
    return devices


class Fastboot:
    # Never flash anything else: the bootloader, radio, vbmeta and super stay stock
    ALLOWED_PARTITIONS = (BOOT_PARTITION,)

    def __init__(self, exe: str, serial: Optional[str] = None):
        self.exe = exe
        self.serial = serial

    def _run(self, args: List[str], timeout: float = 30) -> subprocess.CompletedProcess:
        serial = ["-s", self.serial] if self.serial else []
        return _run_tool([self.exe] + serial + args, timeout)

    def devices(self) -> List[str]:
        result = _run_tool([self.exe, "devices"], 30)
        return [serial for serial, _state in parse_device_list(result.stdout)]

    def getvar(self, name: str) -> Optional[str]:
        result = self._run(["getvar", name])
        # fastboot prints variables on stderr
        return parse_getvar(result.stderr + "\n" + result.stdout, name)

    def flash(self, partition: str, image: Path) -> None:
        if partition not in self.ALLOWED_PARTITIONS:
            raise ValueError(f"refusing to flash {partition}")
        result = self._run(["flash", partition, str(image)], timeout=300)
        if result.returncode != 0:
            raise InstallError(f"fastboot flash {partition} {image.name} failed:\n"
                               + (result.stderr + result.stdout).strip())

    def reboot(self) -> None:
        result = self._run(["reboot"])
        if result.returncode != 0:
            raise InstallError("fastboot reboot failed:\n" + (result.stderr + result.stdout).strip())


class Adb:
    def __init__(self, exe: str):
        self.exe = exe

    def devices(self) -> List[Tuple[str, str]]:
        try:
            return parse_device_list(_run_tool([self.exe, "devices"], 30).stdout)
        except InstallError as e:
            debug(f"adb: {e}")
            return []

    def product(self, serial: str) -> str:
        result = _run_tool([self.exe, "-s", serial, "shell", "getprop", "ro.product.device"], 30)
        return result.stdout.strip() if result.returncode == 0 else ""

    def reboot_bootloader(self, serial: str) -> bool:
        return _run_tool([self.exe, "-s", serial, "reboot", "bootloader"], 30).returncode == 0


BOOTLOADER_HELP = ("To start the bootloader: turn the phone off, then hold Power and Volume Down "
                   "until the fastboot screen appears, and connect it over USB.")


def find_phone(fastboot: Fastboot, adb: Optional[Adb], assume_yes: bool,
               timeout: float = FIND_PHONE_WAIT) -> str:
    """Waits for exactly one phone in fastboot mode and returns its serial."""
    deadline = time.monotonic() + timeout
    offered_adb = waiting = False
    while True:
        devices = fastboot.devices()
        if len(devices) == 1:
            return devices[0]
        if len(devices) > 1:
            raise InstallError(f"{len(devices)} devices are in fastboot mode ({', '.join(devices)}). "
                               "Connect only the Pixel 6a.")
        if adb is not None and not offered_adb:
            states = adb.devices()
            ready = [serial for serial, state in states if state == "device"]
            if len(ready) == 1 and adb.product(ready[0]) != PRODUCT:
                offered_adb = True
                info(f"The device connected over adb ({ready[0]}) isn't a Pixel 6a, so it's left alone.")
            elif len(ready) == 1:
                offered_adb = True
                info("The phone is connected over adb, not in fastboot mode. From Android, adb can "
                     "restart it into the bootloader. (From Ubuntu Touch it can't: hold Power and "
                     "Volume Down instead.)")
                if assume_yes or ask("Restart it into the bootloader with adb now? [Y/n] ") in ("", "y", "yes"):
                    if not adb.reboot_bootloader(ready[0]):
                        info("adb couldn't restart the phone. " + BOOTLOADER_HELP)
            elif any(state == "unauthorized" for _serial, state in states):
                offered_adb = True
                info("The phone is connected over adb but hasn't allowed this computer. Allow it on "
                     "the phone's screen, or: " + BOOTLOADER_HELP)
        if not waiting:
            waiting = True
            info(f"Waiting for the phone in fastboot mode (up to {timeout:.0f} s)...\n" + BOOTLOADER_HELP)
        if time.monotonic() >= deadline:
            raise InstallError("No phone in fastboot mode. " + BOOTLOADER_HELP)
        time.sleep(2)


def wait_for_fastboot(fastboot: Fastboot, serial: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while serial not in fastboot.devices():
        if time.monotonic() >= deadline:
            raise InstallError(f"The phone didn't come back in fastboot mode within {timeout:.0f} s.")
        time.sleep(1)


PHONE_VARS = ("product", "is-userspace", "unlocked", "current-slot", "version-bootloader",
              "version-baseband", "battery-soc", "battery-soc-ok")


def read_phone_vars(fastboot: Fastboot) -> Dict[str, Optional[str]]:
    return {name: fastboot.getvar(name) for name in PHONE_VARS}


def parse_battery(value: Optional[str]) -> Optional[int]:
    match = re.match(r"^(\d+)", value or "")
    return int(match.group(1)) if match else None


STOCK_STEP_URL = f"{DOCS_URL}#step-2-put-stock-android-bp4a251205006-on-the-phone"
FACTORY_IMAGE_HELP = (f"Flash Google's factory image for {STOCK_BUILD} first:\n"
                      f"  {FACTORY_IMAGE_URL}\n"
                      f"  (sha256 {FACTORY_IMAGE_SHA256})\n"
                      "Before you run its flash-all.sh, follow step 2 of the install guide: it checks "
                      "for bootloader anti-rollback and flashes the bootloader to slot a first. "
                      f"flash-all.sh wipes the phone.\n  {STOCK_STEP_URL}\n"
                      "Then run this installer again.")


def check_phone(phone: Dict[str, Optional[str]]) -> Tuple[List[str], List[str]]:
    """Checks the bootloader variables. Returns (errors, warnings)."""
    errors, warnings = [], []
    product = phone.get("product")
    if product != PRODUCT:
        errors.append(f"The device in fastboot mode is '{product or 'unknown'}', not '{PRODUCT}'. "
                      "This installer is only for the Google Pixel 6a.")
        return errors, warnings
    if phone.get("is-userspace") == "yes":
        errors.append("The phone is in fastbootd, not in the bootloader. Restart it into the "
                      "bootloader with: fastboot reboot bootloader")
        return errors, warnings
    if phone.get("unlocked") != "yes":
        errors.append("The bootloader is locked. Unlock it first, which wipes the phone: in "
                      "Android, turn on Settings > System > Developer options > OEM unlocking, "
                      "restart into the bootloader, run 'fastboot flashing unlock' and confirm on "
                      "the phone.")
    slot = (phone.get("current-slot") or "").lstrip("_")
    if slot != "a":
        errors.append(f"The phone runs from slot {slot or 'unknown'}, but this port installs to "
                      "slot a only. Don't just switch slots: slot a may hold an older bootloader. "
                      f"Follow step 2 of the install guide, which flashes the {STOCK_BUILD} "
                      f"bootloader to slot a before it switches:\n  {STOCK_STEP_URL}")
    bootloader = phone.get("version-bootloader")
    if slot == "a" and bootloader != STOCK_BOOTLOADER:
        errors.append(f"The phone has bootloader {bootloader or 'unknown'}, but this port needs "
                      f"stock Android {STOCK_BUILD} (bootloader {STOCK_BOOTLOADER}): the Ubuntu "
                      "Touch images only work with that build's vendor partitions.\n"
                      + FACTORY_IMAGE_HELP)
    baseband = phone.get("version-baseband")
    if baseband and baseband != STOCK_BASEBAND:
        warnings.append(f"The modem firmware is {baseband}, not {STOCK_BASEBAND} from {STOCK_BUILD}. "
                        "Calls and mobile data may not work.")
    battery = parse_battery(phone.get("battery-soc"))
    if battery is None:
        if phone.get("battery-soc-ok") == "no":
            errors.append(f"The battery is too low. Charge the phone to at least {MIN_BATTERY}% first.")
        else:
            warnings.append("Couldn't read the battery level. Make sure the phone is charged to at "
                            f"least {MIN_BATTERY}%.")
    elif battery < MIN_BATTERY:
        errors.append(f"The battery is at {battery}%. Charge the phone to at least {MIN_BATTERY}% first.")
    return errors, warnings


# The installer's shell (busybox telnetd and ash) ############################

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class TelnetFilter:
    """Separates the shell's output from telnet commands, and refuses every
    option the server asks for: a plain byte stream is all this client needs."""

    def __init__(self):
        self._state = "data"
        self._verb = 0

    def feed(self, data: bytes) -> Tuple[bytes, bytes]:
        """Returns (output, reply to send)."""
        out = bytearray()
        reply = bytearray()
        for byte in data:
            state = self._state
            if state == "data":
                if byte == IAC:
                    self._state = "iac"
                else:
                    out.append(byte)
            elif state == "iac":
                if byte == IAC:
                    out.append(IAC)
                    self._state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self._verb = byte
                    self._state = "option"
                elif byte == SB:
                    self._state = "sb"
                else:
                    self._state = "data"
            elif state == "option":
                if self._verb == DO:
                    reply += bytes((IAC, WONT, byte))
                elif self._verb == WILL:
                    reply += bytes((IAC, DONT, byte))
                self._state = "data"
            elif state == "sb":
                if byte == IAC:
                    self._state = "sb-iac"
            elif state == "sb-iac":
                self._state = "data" if byte == SE else "sb"
        return bytes(out), bytes(reply)


_ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[@-Z\\-_=>])")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_terminal_output(text: str) -> str:
    """Drops terminal escapes (the line editor asks for the cursor position
    with ESC[6n) and control characters, and turns CRLF and CR into LF."""
    text = _ESCAPES.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROLS.sub("", text)


def frame_command(command: str, n: int) -> str:
    """Wraps a command in begin and end markers; the end marker carries the
    exit status. The quotes keep the shell's echo of the command line from
    looking like the markers."""
    return f"echo __B''{n}__; {command}; echo __E''{n}__$?"


def parse_framed(text: str, n: int) -> Optional[Tuple[int, str]]:
    """(exit status, output) of command n, or None until all of it has arrived."""
    text = "\n" + clean_terminal_output(text)
    begin = f"\n__B{n}__\n"
    start = text.find(begin)
    if start < 0:
        return None
    body = text[start + len(begin):]
    end = re.search(r"__E%d__(\d+)\n" % n, body)
    if not end:
        return None
    return int(end.group(1)), body[:end.start()]


class PhoneCommandError(InstallError):
    def __init__(self, command: str, status: int, output: str):
        super().__init__(f"This command failed on the phone (exit status {status}):\n  {command}"
                         + (f"\n{output.rstrip()}" if output.strip() else ""))
        self.status = status
        self.output = output


class PhoneShell:
    """Runs commands in the installer's root shell over telnet."""

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._telnet = TelnetFilter()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._count = 0

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "PhoneShell":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _receive(self, deadline: float) -> str:
        self._sock.settimeout(max(0.1, min(deadline - time.monotonic(), 5)))
        try:
            data = self._sock.recv(65536)
        except socket.timeout:
            return ""
        except OSError as e:
            raise InstallError(f"Lost the connection to the phone's shell: {e}") from None
        if not data:
            raise InstallError("The phone closed the shell connection.")
        output, reply = self._telnet.feed(data)
        if reply:
            try:
                self._sock.sendall(reply)
            except OSError as e:
                raise InstallError(f"Lost the connection to the phone's shell: {e}") from None
        return self._decoder.decode(output)

    def _send_line(self, line: str) -> None:
        if "\n" in line or "\r" in line or len(line) > MAX_COMMAND_LINE:
            raise ValueError(f"can't send this command line: {line!r}")
        try:
            self._sock.sendall(line.encode() + b"\r\n")
        except OSError as e:
            raise InstallError(f"Lost the connection to the phone's shell: {e}") from None

    def try_run(self, command: str, timeout: float = 60) -> Tuple[int, str]:
        """Runs a command and returns (exit status, output)."""
        self._count += 1
        n = self._count
        debug(f"phone# {command}")
        self._send_line(frame_command(command, n))
        deadline = time.monotonic() + timeout
        received = ""
        while True:
            result = parse_framed(received, n)
            if result is not None:
                break
            if time.monotonic() >= deadline:
                raise InstallError(f"The phone didn't finish this command within {timeout:.0f} s:\n"
                                   f"  {command}")
            received += self._receive(deadline)
        status, output = result
        for line in output.splitlines():
            debug(f"  {line}")
        if status:
            debug(f"  (exit status {status})")
        return status, output

    def run(self, command: str, timeout: float = 60) -> str:
        """Runs a command and returns its output; fails if it fails."""
        status, output = self.try_run(command, timeout)
        if status != 0:
            raise PhoneCommandError(command, status, output)
        return output

    def send(self, command: str) -> None:
        """Sends a command without waiting for it (reboot)."""
        debug(f"phone# {command}")
        self._send_line(command)


def connect_shell(serial: str, timeout: float = INSTALLER_WAIT) -> PhoneShell:
    info(f"Waiting for the installer on the phone ({PHONE_IP}, up to {timeout:.0f} s). "
         "The phone shows the Google logo meanwhile.")
    deadline = time.monotonic() + timeout
    last_error = None  # type: Optional[Exception]
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection((PHONE_IP, TELNET_PORT), timeout=3)
        except OSError as e:
            last_error = e
            time.sleep(1)
            continue
        shell = PhoneShell(sock)
        try:
            cmdline = shell.run("cat /proc/cmdline", timeout=20)
        except InstallError as e:
            shell.close()
            last_error = e
            time.sleep(1)
            continue
        # Make sure this is our installer and not another device at that address
        if INSTALLER_CMDLINE not in cmdline.split():
            shell.close()
            raise InstallError(f"The device at {PHONE_IP} isn't the installer ({INSTALLER_CMDLINE} "
                               "isn't in its kernel command line). Does your network use "
                               "192.168.2.x? Disconnect from it and try again.")
        # ...and the phone that passed the checks in fastboot mode
        found = shell.run("sed -n 's/^androidboot.serialno = \"\\(.*\\)\"$/\\1/p' /proc/bootconfig",
                          timeout=20).strip()
        if found != serial:
            shell.close()
            raise InstallError(f"The installer at {PHONE_IP} runs on the phone {found or '(unknown)'}, "
                               f"not on {serial}. Connect only one phone and try again.")
        return shell
    raise InstallError(
        f"The installer didn't answer at {PHONE_IP}:{TELNET_PORT} within {timeout:.0f} s "
        f"(last error: {last_error}).\n"
        "The phone should show the Google logo and appear as a USB network device; the computer "
        "gets an address in 192.168.2.20-90 from it (DHCP). Check that this network interface is "
        "up (on Linux, NetworkManager may need you to connect it), that no VPN or other "
        "192.168.2.x network takes the address, and on macOS 15 or newer that your terminal app "
        "may use the local network (System Settings > Privacy & Security > Local Network).")


# Userdata on the phone ######################################################

class Mount(NamedTuple):
    index: int        # position in mountinfo
    dev: str          # major:minor of the filesystem
    root: str         # directory of the filesystem mounted ("/", or a bind-mounted subdirectory)
    mount_point: str
    fstype: str
    source: str


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> List[Mount]:
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-", 6)
        except ValueError:
            continue
        if len(fields) < separator + 3:
            continue
        mounts.append(Mount(len(mounts), fields[2], _unescape(fields[3]), _unescape(fields[4]),
                            fields[separator + 1], _unescape(fields[separator + 2])))
    return mounts


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _depth(path: str) -> int:
    return len([part for part in path.split("/") if part])


def _is_userdata(mount: Mount, userdata_dev: str) -> bool:
    return mount.dev == userdata_dev and mount.root == "/"


def mounts_to_release(mounts: List[Mount], userdata_dev: str) -> List[Mount]:
    """Mounts that keep userdata busy: the initramfs's mounts of userdata, of
    the images on it and of the Android partitions inside them."""
    return [m for m in mounts if m.mount_point != "/" and (
        m.dev == userdata_dev or m.source.startswith("/dev/loop")
        or any(_is_under(m.mount_point, root) for root in INITRAMFS_MOUNT_ROOTS))]


def nested_userdata_mount(targets: List[Mount], userdata_dev: str) -> Optional[Mount]:
    """The userdata mount if it sits inside another mount to release.

    In a normal boot userdata is at /root/userdata, inside the rootfs image
    that lives on it: neither can be unmounted first, so userdata is moved
    out of the way.
    """
    for mount in targets:
        if _is_userdata(mount, userdata_dev) and any(
                other.mount_point != mount.mount_point and _is_under(mount.mount_point, other.mount_point)
                for other in targets):
            return mount
    return None


def free_mount_dir(mounts: List[Mount]) -> str:
    for directory in ("/tmpmnt", "/mnt/old-userdata"):
        if not any(_is_under(m.mount_point, directory) for m in mounts):
            return directory
    raise InstallError("No free directory to move the userdata mount to.")


def unmount_order(targets: List[Mount], userdata_dev: str) -> List[Mount]:
    """Deepest mount points first, later mounts on the same point first, and
    userdata itself last: the loop devices of its images go away with their
    mounts."""
    return sorted(targets, key=lambda m: (_is_userdata(m, userdata_dev), -_depth(m.mount_point), -m.index))


def parse_swap_files(text: str) -> List[str]:
    """Swap files in /proc/swaps (the initramfs turns on /userdata/SWAP.img)."""
    files = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "file":
            files.append(_unescape(fields[0]))
    return files


def parse_df_available(text: str) -> int:
    """Available bytes from "df -P -k" output."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].startswith("Filesystem"):
        lines = lines[1:]
    fields = " ".join(lines).split()
    try:
        return int(fields[-3]) * 1024
    except (IndexError, ValueError):
        raise InstallError(f"Couldn't read the free space on the phone:\n{text}") from None


def parse_sha256sum(text: str) -> str:
    match = re.search(r"\b([0-9a-f]{64})\b", text)
    if not match:
        raise InstallError(f"Couldn't read the SHA-256 from the phone:\n{text}")
    return match.group(1)


PHONE_TOOLS = ("mke2fs", "nc", "sha256sum", "dd", "mount", "umount", "setsid", "blkid")


def check_phone_tools(shell: PhoneShell) -> None:
    missing = [tool for tool in PHONE_TOOLS
               if shell.try_run(f"command -v {tool} >/dev/null")[0] != 0]
    if missing:
        raise InstallError("The installer on the phone lacks " + ", ".join(missing)
                           + ". Is boot-installer.img from the same build as boot.img?")


def userdata_device(shell: PhoneShell) -> str:
    """major:minor of the userdata partition."""
    output = shell.run(f"d=$(readlink -f {USERDATA_DEV}) && cat /sys/class/block/${{d##*/}}/dev")
    match = re.search(r"\b(\d+:\d+)\b", output)
    if not match:
        raise InstallError(f"Couldn't find the userdata partition ({USERDATA_DEV}):\n{output}")
    return match.group(1)


def release_userdata(shell: PhoneShell, userdata_dev: str) -> None:
    """Unmounts everything that uses userdata, so that it can be formatted."""
    for path in parse_swap_files(shell.run("cat /proc/swaps")):
        shell.run(f"swapoff {shlex.quote(path)}")
    moves = stalled = 0
    while True:
        mounts = parse_mountinfo(shell.run("cat /proc/self/mountinfo"))
        targets = mounts_to_release(mounts, userdata_dev)
        if not targets:
            return
        nested = nested_userdata_mount(targets, userdata_dev)
        if nested is not None and moves < 2:
            moves += 1
            directory = free_mount_dir(mounts)
            shell.run(f"mkdir -p {directory} && mount --move {shlex.quote(nested.mount_point)} {directory}")
            continue
        released = False
        errors = []
        for mount in unmount_order(targets, userdata_dev):
            status, output = shell.try_run(f"umount {shlex.quote(mount.mount_point)}")
            if status == 0:
                released = True
            else:
                errors.append(f"  {mount.mount_point}: {output.strip()}")
        if released:
            stalled = 0
            continue
        # The kernel releases a loop device shortly after its unmount, and
        # only then the file on userdata behind it
        stalled += 1
        if stalled >= 15:
            raise InstallError("Couldn't unmount userdata on the phone:\n" + "\n".join(errors))
        time.sleep(1)


def format_userdata(shell: PhoneShell) -> None:
    """Formats userdata, which must not be in use, and mounts it at MOUNT_DIR."""
    # The initramfs's mke2fs (1.43) makes a filesystem that its e2fsck, which
    # checks userdata at every boot, understands. One -F still refuses a
    # mounted device.
    shell.run(f"mke2fs -F -t ext4 -L userdata {USERDATA_DEV} </dev/null", timeout=900)
    shell.run(f"mkdir -p {MOUNT_DIR} && mount -t ext4 {USERDATA_DEV} {MOUNT_DIR}")


def open_existing_userdata(shell: PhoneShell) -> Tuple[str, bool]:
    """Finds the userdata of an existing install. Returns (mount point, whether
    the installer mounted it)."""
    userdata_dev = userdata_device(shell)
    mounts = parse_mountinfo(shell.run("cat /proc/self/mountinfo"))
    mounted = [m for m in mounts if _is_userdata(m, userdata_dev)]
    mounted_here = False
    if mounted:
        mount_point = mounted[0].mount_point
        if mounted[0].fstype != "ext4":
            raise InstallError(f"userdata is {mounted[0].fstype}, not ext4: there's no Ubuntu Touch "
                               "install to keep. Run the installer without --keep-data.")
    else:
        _status, fstype = shell.try_run(f"blkid -o value -s TYPE {USERDATA_DEV}")
        if fstype.strip() != "ext4":
            raise InstallError(f"userdata is {fstype.strip() or 'not formatted'}, not ext4: there's "
                               "no Ubuntu Touch install to keep. Run the installer without --keep-data.")
        shell.run(f"mkdir -p {MOUNT_DIR} && mount -t ext4 {USERDATA_DEV} {MOUNT_DIR}")
        mount_point = MOUNT_DIR
        mounted_here = True
    status, _ = shell.try_run(f"[ -f {shlex.quote(mount_point + '/' + ROOTFS_FILE)} ]")
    if status != 0:
        raise InstallError(f"userdata has no {ROOTFS_FILE}: there's no Ubuntu Touch install to keep. "
                           "Run the installer without --keep-data.")
    return mount_point, mounted_here


def copy_system_image(shell: PhoneShell, system: SystemImage, mount_point: str) -> None:
    """Streams the image to <userdata>/ubuntu.img.new, checks it and moves it
    over ubuntu.img. The old image may be the mounted rootfs; it stays usable
    until the phone restarts."""
    target = f"{mount_point}/{ROOTFS_FILE}"
    new = target + ".new"
    if not re.fullmatch(r"[\w./-]+", new):
        raise InstallError(f"Unexpected path on the phone: {new!r}")
    shell.run(f"rm -f {new}")
    needed = system.size + (64 << 20)
    try:
        available = parse_df_available(shell.run(f"df -P -k {mount_point}"))
    except InstallError as e:
        # nc fails when userdata is full, and then the installer stops
        warn(f"Couldn't check the free space on userdata. {e}")
        available = needed
    if available < needed:
        raise InstallError(f"There isn't enough space on userdata: {gib(needed)} needed, "
                           f"{gib(available)} free.")

    # nc runs in its own session so it doesn't depend on this shell; its exit
    # status tells when it has written everything
    shell.run(f"rm -f {NC_STATUS}; (setsid sh -c 'nc -l -p {STREAM_PORT} > {new}; "
              f"echo $? > {NC_STATUS}' </dev/null >/dev/null 2>&1 &)")
    step(f"Copying the system image ({gib(system.size)})")
    try:
        digest, sent = send_image(system.chunks(), PHONE_IP, STREAM_PORT, system.size)
    except OSError as e:
        raise InstallError(f"Lost the connection to the phone while copying: {e}") from None
    _status, nc_status = shell.try_run(f"i=0; while [ ! -s {NC_STATUS} ] && [ $i -lt 120 ]; do "
                                       f"sleep 1; i=$((i+1)); done; cat {NC_STATUS}", timeout=150)
    if nc_status.strip() != "0":
        raise InstallError("Writing the image on the phone failed (nc exit status "
                           f"{nc_status.strip() or 'unknown: it did not finish'}).")

    step("Checking the copy on the phone (about a minute)")
    phone_digest = parse_sha256sum(shell.run(f"sha256sum {new}", timeout=900))
    if phone_digest != digest:
        # The size tells a short copy from a damaged one
        _status, listing = shell.try_run(f"ls -l {new}")
        fields = listing.split()
        size = fields[4] if len(fields) > 4 else "?"
        shell.try_run(f"rm -f {new}")
        raise InstallError(f"The copy on the phone doesn't match: sent {sent} bytes with sha256 "
                           f"{digest}, the phone has {size} bytes with sha256 {phone_digest}. "
                           "Try again.")
    info(f"  OK: sha256 {digest}")
    shell.run(f"mv -f {new} {target}")


def set_dev_access(shell: PhoneShell, mount_point: str, mode: Optional[str]) -> None:
    if mode is None:
        return
    # Forget the saved USB mode too, so the image's default (charging only)
    # applies unless a marker asks for adb or SSH again
    commands = [f"rm -f {shlex.quote(f'{mount_point}/{USB_MODED_SAVED}')}"]
    for name, marker in sorted(DEV_MARKERS.items()):
        path = shlex.quote(f"{mount_point}/{marker}")
        commands.append(f"touch {path}" if name == mode else f"rm -f {path}")
    shell.run(" && ".join(commands))


def reboot_to_bootloader(shell: PhoneShell) -> None:
    # Only the command field of the bootloader control block: the bootloader
    # clears it when it reads it
    shell.run(f"printf bootonce-bootloader | dd of={MISC_DEV} bs=32 count=1 conv=notrunc,sync")
    command = shell.run(f"dd if={MISC_DEV} bs=32 count=1 2>/dev/null | tr -d '\\000'")
    if command.strip() != "bootonce-bootloader":
        raise InstallError(f"Couldn't set the boot command in misc (it reads {command.strip()!r}).")
    shell.run("sync", timeout=600)
    shell.send("reboot -f")
    time.sleep(3)


# The installation ###########################################################

class State:
    """How far the installation got, for advice after a failure."""

    UNTOUCHED, INSTALLER, SYSTEM_INSTALLED, DONE = range(4)

    def __init__(self, keep_data: bool):
        self.stage = State.UNTOUCHED
        self.keep_data = keep_data
        self.data_erased = False
        self.boot_image = "boot.img"

    def advice(self) -> str:
        if self.stage == State.UNTOUCHED:
            return "Nothing on the phone was changed."
        if self.stage == State.DONE:
            return ""
        restore = (f"To go back to Android instead, flash Google's factory image for {STOCK_BUILD} "
                   f"with flash-all.sh (this wipes the phone):\n  {FACTORY_IMAGE_URL}")
        bootloader = "hold Power and Volume Down until the bootloader (fastboot mode) appears"
        if self.stage == State.SYSTEM_INSTALLED:
            return ("The system image is installed; only the boot image is missing. To finish, "
                    f"{bootloader}, then run:\n"
                    f"  fastboot flash {BOOT_PARTITION} {shlex.quote(self.boot_image)}\n"
                    "  fastboot reboot\n"
                    "or run this installer again with --keep-data.\n" + restore)
        if self.data_erased:
            data = "The phone's data has already been erased. "
        elif self.keep_data:
            data = "Your data and the installed system are unchanged. "
        else:
            data = "Userdata wasn't touched. "
        advice = (f"{BOOT_PARTITION} now holds the installer, so the phone starts into it. {data}"
                  f"To try again: {bootloader}, then run this installer again.\n")
        if not self.data_erased and not self.keep_data:
            advice += ("If the phone ran Android before, flashing the boot.img from Google's factory "
                       f"image (fastboot flash {BOOT_PARTITION} boot.img) starts Android again with its "
                       "data.\n")
        return advice + restore


def describe_plan(serial: str, system: SystemImage, keep_data: bool, dev_access: Optional[str]) -> str:
    if keep_data:
        data_step = f"Keep userdata (your files and settings) and replace /userdata/{ROOTFS_FILE}."
    else:
        data_step = "Format userdata as ext4. THIS ERASES ALL DATA ON THE PHONE."
    if dev_access is None:
        access = "unchanged" if keep_data else "off"
    elif dev_access == "none":
        access = "off"
    else:
        access = f"{dev_access} (/userdata/{DEV_MARKERS[dev_access]})"
    return "\n".join([
        f"Ready to install Ubuntu Touch on the Pixel 6a {serial}:",
        f"  1. Flash boot-installer.img to {BOOT_PARTITION} and start the phone into it.",
        f"  2. {data_step}",
        f"  3. Copy {system.describe()} to /userdata/{ROOTFS_FILE} and check it.",
        f"  4. Restart into the bootloader, flash boot.img to {BOOT_PARTITION} and start Ubuntu Touch.",
        f"Developer access (adb or SSH): {access}.",
        "Nothing else changes: the bootloader, radio, vbmeta and stock Android's super partition "
        "stay as they are.",
        "This takes about 5 minutes. Keep the phone connected until the installer says it's done.",
    ])


def confirm(plan: str, erase: bool, assume_yes: bool) -> None:
    info()
    info(plan)
    info()
    if assume_yes:
        return
    if erase:
        ok = ask('Type "yes" to erase the phone and install: ') == "yes"
    else:
        ok = ask("Continue? [y/N] ") in ("y", "yes")
    if not ok:
        raise Cancelled()


def next_steps(dev_access: Optional[str]) -> str:
    lines = [
        "Done. Ubuntu Touch is starting.",
        "  - The first start takes about a minute (the unlocked-bootloader warning and the Google",
        "    logo come first). Then the welcome wizard sets up language, Wi-Fi and a passcode.",
    ]
    if dev_access == "adb":
        lines.append("  - adb is on once the user interface has started.")
    elif dev_access == "ssh":
        lines.append("  - USB networking and SSH stay on: ssh -p 8022 phablet@10.15.19.82")
        lines.append("    Until you set a passcode, SSH accepts phablet without a password.")
    lines.append(f"  - Updates, debugging and going back to Android: {DOCS_URL}")
    return "\n".join(lines)


def install(args: argparse.Namespace, state: State) -> None:
    folder = (args.images or Path(__file__).resolve().parent).expanduser().resolve()
    step(f"Checking the files in {folder}")
    images = find_images(folder)
    state.boot_image = str(images.boot)
    sums = verify_sha256sums(folder)
    if sums is None:
        warn("No SHA256SUMS in the folder: the files weren't checked for download errors.")
    else:
        unlisted = [p.name for p in (images.boot, images.installer, images.system) if p.name not in sums]
        if unlisted:
            warn("Not listed in SHA256SUMS, so not checked: " + ", ".join(unlisted))
        info("  The files listed in SHA256SUMS match.")
    check_boot_images(images)
    tools = check_host_tools(need_zstd=images.system.suffix == ".zst")
    system = SystemImage(images.system, tools.zstd)
    system.inspect()
    info(f"  System image: {system.describe()}")

    step("Looking for the phone")
    fastboot = Fastboot(tools.fastboot)
    serial = find_phone(fastboot, Adb(tools.adb) if tools.adb else None, args.yes)
    fastboot.serial = serial
    phone = read_phone_vars(fastboot)
    errors, warnings = check_phone(phone)
    for message in warnings:
        warn(message)
    if errors:
        raise InstallError("\n\n".join(errors))
    battery = parse_battery(phone.get("battery-soc"))
    info(f"  Pixel 6a {serial}: bootloader {phone['version-bootloader']}, slot a, battery "
         + (f"{battery}%" if battery is not None else "unknown"))
    confirm(describe_plan(serial, system, args.keep_data, args.dev_access),
            erase=not args.keep_data, assume_yes=args.yes)

    step("Starting the installer on the phone")
    state.stage = State.INSTALLER
    fastboot.flash(BOOT_PARTITION, images.installer)
    fastboot.reboot()
    with connect_shell(serial) as shell:
        check_phone_tools(shell)
        if args.keep_data:
            step("Opening userdata")
            mount_point, mounted_here = open_existing_userdata(shell)
        else:
            step("Formatting userdata (this can take a minute)")
            release_userdata(shell, userdata_device(shell))
            state.data_erased = True
            format_userdata(shell)
            mount_point, mounted_here = MOUNT_DIR, True
        copy_system_image(shell, system, mount_point)
        state.stage = State.SYSTEM_INSTALLED
        set_dev_access(shell, mount_point, args.dev_access)
        shell.run("sync", timeout=600)
        if mounted_here:
            shell.try_run(f"umount {mount_point}")
        step("Restarting into the bootloader")
        reboot_to_bootloader(shell)
    wait_for_fastboot(fastboot, serial, BOOTLOADER_WAIT)

    step("Flashing boot.img")
    fastboot.flash(BOOT_PARTITION, images.boot)
    state.stage = State.DONE
    fastboot.reboot()
    info()
    info(next_steps(args.dev_access))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Install Ubuntu Touch on a Google Pixel 6a (bluejay).\n\n"
                    f"The phone must run stock Android {STOCK_BUILD} with an unlocked\n"
                    "bootloader, and be in its bootloader (fastboot mode).",
        epilog=f"Instructions: {DOCS_URL}")
    parser.add_argument("--keep-data", action="store_true",
                        help="keep userdata (your files and settings) and only replace the system "
                             "image; needs an existing install of this port")
    parser.add_argument("--dev-access", choices=("none", "adb", "ssh"),
                        help="adb, or USB networking with SSH, after the system starts; none turns "
                             "both off (default: leave as it is; off on a new install)")
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    parser.add_argument("--images", metavar="DIR", type=Path,
                        help="folder with boot.img, boot-installer.img and system.img.zst or "
                             "system.img (default: the folder of this script)")
    parser.add_argument("--verbose", action="store_true",
                        help="show the commands run on the computer and on the phone")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    global _verbose
    args = parse_args(argv)
    _verbose = args.verbose
    state = State(args.keep_data)
    try:
        install(args, state)
    except Cancelled:
        print("\nCancelled. Nothing on the phone was changed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\nInterrupted.\n{state.advice()}", file=sys.stderr)
        return 130
    except InstallError as e:
        print(f"\nError: {e}\n\n{state.advice()}".rstrip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
