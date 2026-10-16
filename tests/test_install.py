"""Unit tests for tools/install.py. Nothing here talks to a phone or the network.

Run with: python3 -m unittest discover -s tests -v
"""

import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("install", ROOT / "tools" / "install.py")
install = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = install
_spec.loader.exec_module(install)


# Android sparse images #######################################################

def sparse_image(block_size, chunks, total_blocks=None, file_header_pad=0, chunk_header_pad=0,
                 major=1):
    """Builds an Android sparse image. chunks: (type, blocks, data) tuples."""
    file_hdr_sz = 28 + file_header_pad
    chunk_hdr_sz = 12 + chunk_header_pad
    if total_blocks is None:
        total_blocks = sum(blocks for ctype, blocks, _ in chunks if ctype != install.CHUNK_CRC32)
    out = struct.pack("<IHHHHIIII", install.SPARSE_MAGIC, major, 0, file_hdr_sz, chunk_hdr_sz,
                      block_size, total_blocks, len(chunks), 0)
    out += b"\0" * file_header_pad
    for ctype, blocks, data in chunks:
        out += struct.pack("<HHII", ctype, 0, blocks, chunk_hdr_sz + len(data))
        out += b"\0" * chunk_header_pad + data
    return out


class SlowReader(io.RawIOBase):
    """Returns a few bytes per read, like a pipe."""

    def __init__(self, data, step=7):
        self._data = data
        self._pos = 0
        self._step = step

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            size = len(self._data)
        n = min(size, self._step)
        piece = self._data[self._pos:self._pos + n]
        self._pos += len(piece)
        return piece


@contextlib.contextmanager
def quiet_stderr_fd():
    """Hides what child processes write to stderr."""
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def ext4_like(size):
    data = bytearray(os.urandom(size))
    data[install.EXT4_MAGIC_OFFSET:install.EXT4_MAGIC_OFFSET + 2] = install.EXT4_MAGIC
    return bytes(data)


class SparseTest(unittest.TestCase):
    BS = 4096

    def setUp(self):
        self.raw1 = os.urandom(2 * self.BS)
        self.raw2 = os.urandom(self.BS)
        self.chunks = [
            (install.CHUNK_RAW, 2, self.raw1),
            (install.CHUNK_FILL, 3, b"\xde\xad\xbe\xef"),
            (install.CHUNK_DONT_CARE, 2, b""),
            (install.CHUNK_CRC32, 0, b"\x01\x02\x03\x04"),
            (install.CHUNK_RAW, 1, self.raw2),
        ]
        self.expected = (self.raw1 + b"\xde\xad\xbe\xef" * (3 * self.BS // 4)
                         + bytes(2 * self.BS) + self.raw2)

    def expand(self, image, step=None):
        reader = SlowReader(image, step) if step else io.BytesIO(image)
        return b"".join(install.iter_image(reader))

    def test_all_chunk_types(self):
        out = self.expand(sparse_image(self.BS, self.chunks))
        self.assertEqual(out, self.expected)
        self.assertEqual(hashlib.sha256(out).hexdigest(), hashlib.sha256(self.expected).hexdigest())

    def test_padded_headers(self):
        image = sparse_image(self.BS, self.chunks, file_header_pad=4, chunk_header_pad=4)
        self.assertEqual(self.expand(image), self.expected)

    def test_short_reads(self):
        self.assertEqual(self.expand(sparse_image(self.BS, self.chunks), step=7), self.expected)

    def test_chunks_larger_than_a_piece(self):
        blocks = 3 * install.PIECE // self.BS + 1
        image = sparse_image(self.BS, [(install.CHUNK_FILL, blocks, b"\x00\x11\x22\x33"),
                                       (install.CHUNK_DONT_CARE, blocks, b"")])
        pieces = list(install.iter_image(io.BytesIO(image)))
        self.assertTrue(all(len(p) <= install.PIECE for p in pieces))
        out = b"".join(pieces)
        self.assertEqual(len(out), 2 * blocks * self.BS)
        self.assertEqual(out[:blocks * self.BS], b"\x00\x11\x22\x33" * (blocks * self.BS // 4))
        self.assertEqual(out[blocks * self.BS:].count(0), blocks * self.BS)

    def test_header(self):
        header = install.parse_sparse_header(sparse_image(self.BS, self.chunks)[:28])
        self.assertEqual(header.size, 8 * self.BS)
        self.assertIsNone(install.parse_sparse_header(ext4_like(4096)[:28]))
        self.assertIsNone(install.parse_sparse_header(b"short"))

    def test_unsupported_version(self):
        with self.assertRaises(install.ImageFormatError):
            self.expand(sparse_image(self.BS, self.chunks, major=2))

    def test_truncated(self):
        image = sparse_image(self.BS, self.chunks)
        for cut in (40, len(image) // 2, len(image) - 1):
            with self.subTest(cut=cut), self.assertRaises(install.ImageFormatError):
                self.expand(image[:cut])

    def test_unknown_chunk_type(self):
        with self.assertRaisesRegex(install.ImageFormatError, "unknown type 0xcac9"):
            self.expand(sparse_image(self.BS, [(0xCAC9, 1, b"")], total_blocks=1))

    def test_block_count_mismatch(self):
        with self.assertRaisesRegex(install.ImageFormatError, "header says"):
            self.expand(sparse_image(self.BS, self.chunks, total_blocks=9))
        with self.assertRaises(install.ImageFormatError):
            self.expand(sparse_image(self.BS, self.chunks, total_blocks=7))

    def test_bad_chunk_sizes(self):
        bad = [
            [(install.CHUNK_RAW, 2, self.raw2)],
            [(install.CHUNK_FILL, 1, b"\x00\x00")],
            [(install.CHUNK_DONT_CARE, 1, b"\x00\x00\x00\x00")],
        ]
        for chunks in bad:
            with self.subTest(chunks=chunks[0][0]), self.assertRaises(install.ImageFormatError):
                self.expand(sparse_image(self.BS, chunks, total_blocks=chunks[0][1]))


class RawImageTest(unittest.TestCase):
    def test_passthrough(self):
        data = ext4_like(3 * install.PIECE + 123)
        self.assertEqual(b"".join(install.iter_image(io.BytesIO(data))), data)
        self.assertEqual(b"".join(install.iter_image(SlowReader(data[:5000], 999))), data[:5000])

    def test_tiny_input(self):
        self.assertEqual(b"".join(install.iter_image(io.BytesIO(b"abc"))), b"abc")
        self.assertEqual(b"".join(install.iter_image(io.BytesIO(b""))), b"")


class SystemImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.raw = os.urandom(4096)
        self.sparse = sparse_image(4096, [(install.CHUNK_RAW, 1, self.raw),
                                          (install.CHUNK_DONT_CARE, 3, b"")])
        self.expected = self.raw + bytes(3 * 4096)

    def write(self, name, data):
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_sparse_file(self):
        image = install.SystemImage(self.write("system.img", self.sparse))
        image.inspect()
        self.assertTrue(image.sparse)
        self.assertEqual(image.size, len(self.expected))
        self.assertEqual(b"".join(image.chunks()), self.expected)

    def test_raw_file(self):
        data = ext4_like(8192)
        image = install.SystemImage(self.write("system.img", data))
        image.inspect()
        self.assertFalse(image.sparse)
        self.assertEqual(image.size, 8192)
        self.assertEqual(b"".join(image.chunks()), data)

    def test_not_an_image(self):
        image = install.SystemImage(self.write("system.img", os.urandom(8192)))
        with self.assertRaises(install.ImageFormatError):
            image.inspect()

    @unittest.skipUnless(shutil.which("zstd"), "zstd isn't installed")
    def test_zstd(self):
        zstd = shutil.which("zstd")
        for name, data, size in (("sparse", self.sparse, len(self.expected)),
                                 ("raw", ext4_like(10000), 10000)):
            with self.subTest(name):
                path = self.write(f"{name}.img.zst", subprocess.run(
                    [zstd, "-q", "-c"], input=data, stdout=subprocess.PIPE, check=True).stdout)
                image = install.SystemImage(path, zstd)
                image.inspect()
                self.assertEqual(image.size, size)
                expected = self.expected if name == "sparse" else data
                self.assertEqual(b"".join(image.chunks()), expected)

    @unittest.skipUnless(shutil.which("zstd"), "zstd isn't installed")
    def test_zstd_broken(self):
        zstd = shutil.which("zstd")
        data = ext4_like(200000)
        compressed = subprocess.run([zstd, "-q", "-c"], input=data, stdout=subprocess.PIPE,
                                    check=True).stdout
        image = install.SystemImage(self.write("system.img.zst", compressed[:-8]), zstd)
        with quiet_stderr_fd(), self.assertRaises(install.ImageFormatError):
            b"".join(image.chunks())

    def test_zstd_is_only_used_for_zst(self):
        image = install.SystemImage(self.write("system.img", self.sparse), "/nonexistent/zstd")
        image.inspect()
        self.assertEqual(image.size, len(self.expected))


class FakeStreamSocket:
    def __init__(self):
        self.received = bytearray()
        self.shut = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.received += data

    def shutdown(self, how):
        self.shut = how == socket.SHUT_WR

    def recv(self, _size):
        return b""


class SendImageTest(unittest.TestCase):
    def test_streams_and_hashes(self):
        fake = FakeStreamSocket()
        pieces = [os.urandom(1000), os.urandom(5), b"", os.urandom(3000)]
        data = b"".join(pieces)
        lines = []
        with mock.patch.object(install, "connect_with_retry", return_value=fake), \
                mock.patch.object(install, "info", lines.append):
            digest, sent = install.send_image(iter(pieces), "phone", 5555, len(data))
        self.assertEqual(bytes(fake.received), data)
        self.assertTrue(fake.shut)
        self.assertEqual(sent, len(data))
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertTrue(lines[-1].lstrip().startswith("100%"))


class ProgressTest(unittest.TestCase):
    def test_every_five_percent(self):
        lines = []
        clock = iter(range(1000)).__next__
        progress = install.Progress(1000, clock=clock, out=lines.append)
        for done in range(0, 1001, 10):
            progress.update(done)
        self.assertEqual(len(lines), 20)
        self.assertTrue(lines[0].lstrip().startswith("5%"))
        self.assertIn("MB/s", lines[0])
        self.assertTrue(lines[-1].lstrip().startswith("100%"))

    def test_big_steps(self):
        lines = []
        progress = install.Progress(100, out=lines.append)
        progress.update(37)
        progress.update(100)
        self.assertEqual([line.split()[0] for line in lines], ["37%", "100%"])


# The installer's shell #######################################################

IAC, DO, DONT, WILL, WONT, SB, SE = 255, 253, 254, 251, 252, 250, 240
NAWS, ECHO, SGA = 31, 1, 3

# What busybox telnetd sends first
TELNETD_HELLO = bytes([IAC, DO, ECHO, IAC, DO, NAWS, IAC, WILL, ECHO, IAC, WILL, SGA])


class TelnetFilterTest(unittest.TestCase):
    def test_refuses_options(self):
        output, reply = install.TelnetFilter().feed(TELNETD_HELLO + b"/ # ")
        self.assertEqual(output, b"/ # ")
        self.assertEqual(reply, bytes([IAC, WONT, ECHO, IAC, WONT, NAWS, IAC, DONT, ECHO, IAC, DONT, SGA]))

    def test_split_across_reads(self):
        telnet = install.TelnetFilter()
        data = b"ab" + TELNETD_HELLO + b"cd" + bytes([IAC, IAC]) + b"e"
        output = reply = b""
        for byte in data:
            o, r = telnet.feed(bytes([byte]))
            output += o
            reply += r
        self.assertEqual(output, b"abcd\xffe")
        self.assertEqual(len(reply), 12)

    def test_subnegotiation_and_commands(self):
        data = (b"x" + bytes([IAC, SB, NAWS, 0, 80, 0, 24, IAC, SE]) + b"y"
                + bytes([IAC, 241]) + b"z" + bytes([IAC, WONT, ECHO]))
        self.assertEqual(install.TelnetFilter().feed(data), (b"xyz", b""))


def shell_transcript(n, command, output, status=0, echo=True):
    """What the phone sends for a framed command, as busybox ash shows it."""
    line = install.frame_command(command, n)
    text = ""
    if echo:
        # The line editor echoes the command, wrapped at 80 columns
        echoed = "/ # \x1b[6n" + line
        text += "\r\n".join(echoed[i:i + 80] for i in range(0, len(echoed), 80)) + "\r\n"
    text += f"__B{n}__\r\n" + output.replace("\n", "\r\n") + f"__E{n}__{status}\r\n/ # \x1b[6n"
    return text


class FramingTest(unittest.TestCase):
    def test_clean_terminal_output(self):
        text = "/ # \x1b[6nls\r\n\x1b[1;34mbin\x1b[0m\r\nmke2fs 1/3\b\b\b2/3\rdone\x07\r\n"
        self.assertEqual(install.clean_terminal_output(text), "/ # ls\nbin\nmke2fs 1/32/3\ndone\n")

    def test_frame_command(self):
        self.assertEqual(install.frame_command("sync", 3), "echo __B''3__; sync; echo __E''3__$?")

    def test_parse(self):
        text = ("\r\n\r\nBusyBox v1.22.1 (Debian 1:1.22.0-19+b3) built-in shell (ash)\r\n"
                + shell_transcript(3, "cat /proc/mounts", "line one\nline two\n"))
        self.assertEqual(install.parse_framed(text, 3), (0, "line one\nline two\n"))

    def test_status_and_empty_output(self):
        self.assertEqual(install.parse_framed(shell_transcript(4, "false", "", 1), 4), (1, ""))
        self.assertEqual(install.parse_framed(shell_transcript(5, "x", "", 127), 5), (127, ""))

    def test_output_without_newline(self):
        text = shell_transcript(6, "printf bootonce-bootloader", "bootonce-bootloader")
        self.assertEqual(install.parse_framed(text, 6), (0, "bootonce-bootloader"))

    def test_incomplete(self):
        text = shell_transcript(7, "sha256sum x", "abc  x\n", 0)
        end = text.index("__E7__")
        for cut in (10, end, end + 6, end + 7):
            with self.subTest(cut=cut):
                self.assertIsNone(install.parse_framed(text[:cut], 7))

    def test_echo_is_not_a_marker(self):
        line = install.frame_command("sleep 100", 8)
        self.assertIsNone(install.parse_framed("/ # " + line + "\r\n", 8))
        # The terminal's echo of a command typed while another one runs
        self.assertIsNone(install.parse_framed(line + "\r\n" + line + "\r\n", 8))

    def test_other_commands_dont_match(self):
        text = shell_transcript(1, "echo a", "a\n") + shell_transcript(11, "echo b", "b\n", 3)
        self.assertEqual(install.parse_framed(text, 1), (0, "a\n"))
        self.assertEqual(install.parse_framed(text, 11), (3, "b\n"))
        self.assertIsNone(install.parse_framed(text, 2))


class FakeTelnetSocket:
    """A busybox telnetd with ash behind it; answers framed commands from a table."""

    def __init__(self, answers, piece=5, hello=TELNETD_HELLO, on_line=None):
        self.answers = answers  # command -> (status, output); missing: no answer
        self.on_line = on_line  # called with lines that aren't framed commands
        self.piece = piece
        self.pending = bytearray(hello + b"\r\nBusyBox v1.22.1 built-in shell (ash)\r\n")
        self.sent = bytearray()
        self.commands = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.sent += data
        match = re.match(rb"echo __B''(\d+)__; (.*); echo __E''\d+__\$\?\r\n$", bytes(data), re.S)
        if not match:
            if self.on_line and data.endswith(b"\r\n"):
                self.on_line(data[:-2].decode())
            return
        n, command = int(match.group(1)), match.group(2).decode()
        self.commands.append(command)
        answer = self.answers(command) if callable(self.answers) else self.answers.get(command)
        if answer is not None:
            status, output = answer
            self.pending += shell_transcript(n, command, output, status).encode()

    def recv(self, _size):
        if not self.pending:
            raise socket.timeout()
        data = bytes(self.pending[:self.piece])
        del self.pending[:self.piece]
        return data

    def close(self):
        self.closed = True


class PhoneShellTest(unittest.TestCase):
    def test_run(self):
        sock = FakeTelnetSocket({"cat /proc/cmdline": (0, "break=bottom console=ttySAC0\n")})
        shell = install.PhoneShell(sock)
        self.assertEqual(shell.run("cat /proc/cmdline"), "break=bottom console=ttySAC0\n")
        self.assertIn(bytes([IAC, WONT, ECHO]), bytes(sock.sent))
        self.assertIn(bytes([IAC, DONT, SGA]), bytes(sock.sent))

    def test_sequence_and_status(self):
        sock = FakeTelnetSocket({"true": (0, ""), "ls /nope": (1, "ls: /nope: No such file\n")})
        shell = install.PhoneShell(sock)
        self.assertEqual(shell.try_run("true"), (0, ""))
        self.assertEqual(shell.try_run("ls /nope"), (1, "ls: /nope: No such file\n"))
        with self.assertRaises(install.PhoneCommandError) as cm:
            shell.run("ls /nope")
        self.assertEqual(cm.exception.status, 1)
        self.assertIn("No such file", str(cm.exception))
        self.assertEqual(sock.commands, ["true", "ls /nope", "ls /nope"])

    def test_timeout(self):
        shell = install.PhoneShell(FakeTelnetSocket({}))
        with self.assertRaisesRegex(install.InstallError, "didn't finish"):
            shell.run("sleep 1000", timeout=0.2)

    def test_closed_connection(self):
        sock = FakeTelnetSocket({})
        sock.recv = lambda _size: b""
        with self.assertRaisesRegex(install.InstallError, "closed"):
            install.PhoneShell(sock).run("true", timeout=5)

    def test_refuses_multiline_and_long_commands(self):
        shell = install.PhoneShell(FakeTelnetSocket({}))
        with self.assertRaises(ValueError):
            shell.run("echo a\necho b")
        with self.assertRaises(ValueError):
            shell.run("echo " + "x" * install.MAX_COMMAND_LINE)


# fastboot ####################################################################

GOOD_PHONE = {
    "product": "bluejay",
    "is-userspace": "no",
    "unlocked": "yes",
    "current-slot": "a",
    "version-bootloader": "bluejay-16.4-14097577",
    "version-baseband": "g5123b-145971-251030-B-14356419",
    "battery-soc": "85",
    "battery-soc-ok": "yes",
}


class FastbootTest(unittest.TestCase):
    def test_parse_getvar(self):
        cases = [
            ("version-bootloader: bluejay-16.4-14097577\nFinished. Total time: 0.001s\n",
             "version-bootloader", "bluejay-16.4-14097577"),
            ("(bootloader) unlocked: yes\nOKAY [  0.000s]\n", "unlocked", "yes"),
            ("(bootloader) current-slot:a\n", "current-slot", "a"),
            ("getvar:battery-soc FAILED (remote: 'GetVar Variable Not found')\n"
             "Finished. Total time: 0.001s\n", "battery-soc", None),
            ("battery-soc-ok: yes\n", "battery-soc", None),
            ("product: bluejay\n", "product", "bluejay"),
            ("", "product", None),
        ]
        for output, name, expected in cases:
            with self.subTest(output=output):
                self.assertEqual(install.parse_getvar(output, name), expected)

    def test_parse_device_list(self):
        self.assertEqual(install.parse_device_list("26061JEGR01234\tfastboot\n"),
                         [("26061JEGR01234", "fastboot")])
        self.assertEqual(install.parse_device_list(""), [])
        adb = ("* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
               "List of devices attached\n26061JEGR01234\tdevice\nX\tunauthorized\n\n")
        self.assertEqual(install.parse_device_list(adb),
                         [("26061JEGR01234", "device"), ("X", "unauthorized")])
        with self.assertRaisesRegex(install.InstallError, "udev"):
            install.parse_device_list("no permissions (missing udev rules?); see [url]\tfastboot\n")

    def fake_run(self, stdout="", stderr="", returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        return mock.patch.object(install.subprocess, "run", return_value=result)

    def test_getvar_reads_stderr(self):
        fastboot = install.Fastboot("fastboot", "SERIAL")
        with self.fake_run(stderr="version-bootloader: bluejay-16.4-14097577\n"
                                  "Finished. Total time: 0.001s\n") as run:
            self.assertEqual(fastboot.getvar("version-bootloader"), "bluejay-16.4-14097577")
        self.assertEqual(run.call_args[0][0], ["fastboot", "-s", "SERIAL", "getvar", "version-bootloader"])

    def test_devices_without_serial(self):
        fastboot = install.Fastboot("fastboot", "SERIAL")
        with self.fake_run(stdout="AAA\tfastboot\nBBB\tfastboot\n") as run:
            self.assertEqual(fastboot.devices(), ["AAA", "BBB"])
        self.assertEqual(run.call_args[0][0], ["fastboot", "devices"])

    def test_flash(self):
        fastboot = install.Fastboot("fastboot", "SERIAL")
        with self.fake_run(stderr="Sending 'boot_a' OKAY\nWriting 'boot_a' OKAY\n") as run:
            fastboot.flash("boot_a", Path("/x/boot.img"))
        self.assertEqual(run.call_args[0][0], ["fastboot", "-s", "SERIAL", "flash", "boot_a", "/x/boot.img"])
        with self.fake_run(stderr="FAILED (remote: 'partition does not exist')\n", returncode=1):
            with self.assertRaisesRegex(install.InstallError, "partition does not exist"):
                fastboot.flash("boot_a", Path("/x/boot.img"))

    def test_flash_only_boot_a(self):
        fastboot = install.Fastboot("fastboot", "SERIAL")
        with self.fake_run() as run:
            for partition in ("boot_b", "bootloader", "radio", "vbmeta_a", "super", "userdata", "misc"):
                with self.subTest(partition), self.assertRaises(ValueError):
                    fastboot.flash(partition, Path("x.img"))
        run.assert_not_called()

    def test_timeout(self):
        fastboot = install.Fastboot("fastboot", "SERIAL")
        with mock.patch.object(install.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("fastboot", 30)):
            with self.assertRaisesRegex(install.InstallError, "didn't finish"):
                fastboot.getvar("product")


class CheckPhoneTest(unittest.TestCase):
    def check(self, **changes):
        phone = dict(GOOD_PHONE)
        phone.update(changes)
        return install.check_phone(phone)

    def test_good(self):
        self.assertEqual(self.check(), ([], []))
        self.assertEqual(self.check(**{"current-slot": "_a"}), ([], []))

    def test_wrong_product(self):
        errors, _ = self.check(product="oriole")
        self.assertEqual(len(errors), 1)
        self.assertIn("only for the Google Pixel 6a", errors[0])

    def test_fastbootd(self):
        errors, _ = self.check(**{"is-userspace": "yes"})
        self.assertIn("fastboot reboot bootloader", errors[0])

    def test_locked(self):
        errors, _ = self.check(unlocked="no")
        self.assertEqual(len(errors), 1)
        self.assertIn("fastboot flashing unlock", errors[0])

    def test_slot_b(self):
        errors, _ = self.check(**{"current-slot": "b"})
        self.assertEqual(len(errors), 1)
        self.assertIn("#step-2-put-stock-android-bp4a251205006-on-the-phone", errors[0])
        self.assertNotIn("--set-active", errors[0])

    def test_other_bootloader(self):
        errors, _ = self.check(**{"version-bootloader": "bluejay-16.4-13000000"})
        self.assertEqual(len(errors), 1)
        self.assertIn(install.STOCK_BUILD, errors[0])
        self.assertIn(install.FACTORY_IMAGE_URL, errors[0])
        self.assertIn(install.FACTORY_IMAGE_SHA256, errors[0])

    def test_battery(self):
        errors, _ = self.check(**{"battery-soc": "29"})
        self.assertIn("29%", errors[0])
        self.assertEqual(self.check(**{"battery-soc": "30"}), ([], []))
        errors, warnings = self.check(**{"battery-soc": None})
        self.assertEqual(errors, [])
        self.assertIn("battery", warnings[0])
        errors, _ = self.check(**{"battery-soc": None, "battery-soc-ok": "no"})
        self.assertIn("battery is too low", errors[0])

    def test_baseband(self):
        errors, warnings = self.check(**{"version-baseband": "g5123b-1"})
        self.assertEqual(errors, [])
        self.assertIn("modem", warnings[0])


# Release files ###############################################################

class Sha256SumsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_parse(self):
        a, b = "a" * 64, "B" * 64
        text = f"# comment\n\n{a}  boot.img\n{b} *system.img.zst\r\n"
        self.assertEqual(install.parse_sha256sums(text), {"boot.img": a, "system.img.zst": b.lower()})

    def test_parse_errors(self):
        for text in ("abc  boot.img\n", "a" * 64 + "boot.img\n",
                     "a" * 64 + "  ../boot.img\n", "a" * 64 + "  /etc/passwd\n", "a" * 64 + "  ..\n"):
            with self.subTest(text=text), self.assertRaises(install.InstallError):
                install.parse_sha256sums(text)

    def test_verify(self):
        files = {"boot.img": b"boot", "install.py": b"script"}
        for name, data in files.items():
            (self.tmp / name).write_bytes(data)
        listing = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items())
        listing += hashlib.sha256(b"gone").hexdigest() + "  system.img.zst\n"
        (self.tmp / "SHA256SUMS").write_text(listing)
        sums = install.verify_sha256sums(self.tmp)
        self.assertEqual(set(sums), {"boot.img", "install.py", "system.img.zst"})
        (self.tmp / "boot.img").write_bytes(b"corrupt")
        with self.assertRaisesRegex(install.InstallError, "boot.img"):
            install.verify_sha256sums(self.tmp)

    def test_no_sums(self):
        self.assertIsNone(install.verify_sha256sums(self.tmp))


def boot_header(cmdline, version=4):
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 40, version)
    if version >= 3:
        header[44:44 + len(cmdline)] = cmdline.encode()
    else:
        header[64:64 + len(cmdline)] = cmdline.encode()
    return bytes(header)


class BootImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def images(self, boot, installer):
        (self.tmp / "boot.img").write_bytes(boot)
        (self.tmp / "boot-installer.img").write_bytes(installer)
        (self.tmp / "system.img.zst").write_bytes(b"x")
        return install.find_images(self.tmp)

    def test_cmdline(self):
        self.assertEqual(install.boot_image_cmdline(boot_header("break=bottom")), "break=bottom")
        self.assertEqual(install.boot_image_cmdline(boot_header("")), "")
        self.assertEqual(install.boot_image_cmdline(boot_header("console=x", version=2)), "console=x")
        with self.assertRaises(ValueError):
            install.boot_image_cmdline(b"\0" * 4096)

    def test_good_pair(self):
        install.check_boot_images(self.images(boot_header(""), boot_header("break=bottom")))

    def test_swapped(self):
        with self.assertRaisesRegex(install.InstallError, "renamed boot.img"):
            install.check_boot_images(self.images(boot_header(""), boot_header("")))
        with self.assertRaisesRegex(install.InstallError, "renamed boot-installer.img"):
            install.check_boot_images(self.images(boot_header("break=bottom"), boot_header("break=bottom")))

    def test_too_big_or_not_boot(self):
        with self.assertRaisesRegex(install.InstallError, "more than boot_a"):
            install.check_boot_images(self.images(boot_header("") + bytes(64 << 20),
                                                  boot_header("break=bottom")))
        with self.assertRaisesRegex(install.InstallError, "isn't an Android boot image"):
            install.check_boot_images(self.images(bytes(4096), boot_header("break=bottom")))

    def test_find_images(self):
        with self.assertRaisesRegex(install.InstallError, "boot-installer.img"):
            (self.tmp / "boot.img").write_bytes(b"x")
            install.find_images(self.tmp)
        images = self.images(b"a", b"b")
        self.assertEqual(images.system.name, "system.img.zst")
        (self.tmp / "system.img.zst").unlink()
        (self.tmp / "system.img").write_bytes(b"x")
        self.assertEqual(install.find_images(self.tmp).system.name, "system.img")


# Userdata on the phone #######################################################

USERDATA = "8:35"

# A normal boot stopped at break=bottom. Moves keep their place: userdata,
# mounted first at /tmpmnt, comes before the rootfs image it holds.
NORMAL_BOOT_MOUNTINFO = """\
1 1 0:1 / / rw - rootfs rootfs rw
20 1 0:20 / /sys rw,nosuid,nodev,noexec,relatime - sysfs sysfs rw
21 1 0:21 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw
22 1 0:5 / /dev rw,nosuid,relatime - devtmpfs udev rw,mode=755
23 22 0:22 / /dev/pts rw,nosuid,noexec,relatime - devpts devpts rw,gid=5,mode=620
24 1 0:23 / /run rw,nosuid,noexec,relatime - tmpfs tmpfs rw,mode=755
30 31 8:35 / /root/userdata rw,relatime - ext4 /dev/sda35 rw,discard,data=journal
31 1 7:0 / /root ro,relatime - ext4 /dev/loop0 ro
32 31 7:1 / /root/var/lib/lxc/android/rootfs ro,relatime - ext4 /dev/loop1 ro
33 31 7:1 / /root/android ro,relatime - ext4 /dev/loop1 ro
34 33 8:35 /android-data /root/android/data rw,relatime - ext4 /dev/sda35 rw,discard,data=journal
35 31 0:30 / /root/run rw,nosuid,noexec,relatime - tmpfs tmpfs rw,mode=755
36 31 0:30 /image.fstab /root/etc/fstab rw,nosuid,noexec,relatime - tmpfs tmpfs rw,mode=755
37 31 8:35 /system-data/etc/writable /root/etc/writable rw,relatime - ext4 /dev/sda35 rw,data=journal
38 33 253:3 / /root/android/vendor ro,relatime - ext4 /dev/mapper/vendor_a ro
39 33 7:1 /system /root/android/system ro,relatime - ext4 /dev/loop1 ro
40 1 0:31 / /config rw,relatime - configfs none rw
41 31 7:1 /system/lib/modules /root/lib/modules ro,relatime - ext4 /dev/loop1 ro
42 1 0:32 / /mnt/my\\040dir rw - tmpfs tmpfs rw
"""

# Loop devices and the filesystem their backing file lives on
LOOP_BACKING = {"7:0": USERDATA, "7:1": "7:0"}


class MountTest(unittest.TestCase):
    def test_parse(self):
        mounts = install.parse_mountinfo(NORMAL_BOOT_MOUNTINFO + "garbage line\n")
        self.assertEqual(len(mounts), 19)
        self.assertEqual(mounts[6], install.Mount(6, "8:35", "/", "/root/userdata", "ext4", "/dev/sda35"))
        self.assertEqual(mounts[-1].mount_point, "/mnt/my dir")

    def test_release_plan(self):
        mounts = install.parse_mountinfo(NORMAL_BOOT_MOUNTINFO)
        targets = install.mounts_to_release(mounts, USERDATA)
        points = {m.mount_point for m in targets}
        self.assertIn("/root", points)
        self.assertIn("/root/android/vendor", points)
        self.assertFalse(points & {"/", "/sys", "/proc", "/dev", "/dev/pts", "/run", "/config", "/mnt/my dir"})
        nested = install.nested_userdata_mount(targets, USERDATA)
        self.assertEqual(nested.mount_point, "/root/userdata")
        self.assertEqual(install.free_mount_dir(mounts), "/tmpmnt")

    def test_unmount_order(self):
        mounts = install.parse_mountinfo(NORMAL_BOOT_MOUNTINFO.replace(" /root/userdata ", " /tmpmnt "))
        targets = install.mounts_to_release(mounts, USERDATA)
        self.assertIsNone(install.nested_userdata_mount(targets, USERDATA))
        order = [m.mount_point for m in install.unmount_order(targets, USERDATA)]
        self.assertEqual(order[-1], "/tmpmnt")
        for i, point in enumerate(order):
            for later in order[i + 1:]:
                self.assertFalse(install._is_under(later, point) and later != point,
                                 f"{point} is unmounted before {later} inside it")

    def test_parse_swap_files(self):
        text = ("Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n"
                "/root/userdata/SWAP.img                 file\t\t1048572\t0\t-2\n"
                "/dev/zram0                              partition\t2097148\t0\t100\n")
        self.assertEqual(install.parse_swap_files(text), ["/root/userdata/SWAP.img"])
        self.assertEqual(install.parse_swap_files("Filename Type Size Used Priority\n"), [])

    def test_parse_df(self):
        text = ("Filesystem           1024-blocks    Used Available Capacity Mounted on\n"
                "/dev/sda35            110000000  5300000 104700000   5% /root/userdata\n")
        self.assertEqual(install.parse_df_available(text), 104700000 * 1024)
        wrapped = ("Filesystem           1K-blocks      Used Available Use% Mounted on\n"
                   "/dev/disk/by-partlabel/userdata\n"
                   "                     110000000  5300000 104700000   5% /mnt/userdata\n")
        self.assertEqual(install.parse_df_available(wrapped), 104700000 * 1024)
        with self.assertRaises(install.InstallError):
            install.parse_df_available("df: /x: No such file or directory\n")

    def test_parse_sha256sum(self):
        digest = hashlib.sha256(b"x").hexdigest()
        self.assertEqual(install.parse_sha256sum(f"{digest}  /mnt/userdata/ubuntu.img.new\n"), digest)
        with self.assertRaises(install.InstallError):
            install.parse_sha256sum("sha256sum: can't open 'x': No such file or directory\n")


class FakeMountShell:
    """The initramfs's mount table, enough to run release_userdata against.

    umount fails like the kernel does while something is mounted inside the
    mount point, or while a loop device or swap file still uses a file on the
    filesystem. A loop device lets go of its file some table reads after its
    last unmount, like the kernel's asynchronous loop release.
    """

    def __init__(self, mountinfo, swaps=("/root/userdata/SWAP.img",), loop_release=1):
        self.lines = [line.split() for line in mountinfo.splitlines()]
        self.swaps = list(swaps)
        self.loop_release = loop_release
        self.releasing = {}  # loop device -> table reads until it lets go
        self.commands = []

    def _users(self):
        users = {LOOP_BACKING[f[2]] for f in self.lines if f[2] in LOOP_BACKING}
        users |= {LOOP_BACKING[dev] for dev in self.releasing}
        if self.swaps:
            users.add(USERDATA)
        return users

    def try_run(self, command, timeout=60):
        self.commands.append(command)
        if command == "cat /proc/swaps":
            return 0, "Filename Type Size Used Priority\n" + "".join(f"{s} file 1048572 0 -2\n" for s in self.swaps)
        match = re.fullmatch(r"swapoff (\S+)", command)
        if match:
            self.swaps.remove(match.group(1))
            return 0, ""
        if command == "cat /proc/self/mountinfo":
            self.releasing = {dev: n - 1 for dev, n in self.releasing.items() if n > 1}
            return 0, "".join(" ".join(f) + "\n" for f in self.lines)
        match = re.fullmatch(r"mkdir -p (\S+) && mount --move (\S+) (\S+)", command)
        if match:
            source, target = match.group(2), match.group(3)
            for fields in self.lines:
                if install._is_under(fields[4], source):
                    fields[4] = target + fields[4][len(source):]
            return 0, ""
        match = re.fullmatch(r"umount (\S+)", command)
        if match:
            point = match.group(1)
            index = max(i for i, f in enumerate(self.lines) if f[4] == point)
            fields = self.lines[index]
            busy = any(install._is_under(f[4], point) and f[4] != point for f in self.lines)
            if fields[3] == "/" and fields[2] in self._users():
                busy = True
            if busy:
                return 1, f"umount: can't unmount {point}: Device or resource busy\n"
            del self.lines[index]
            if fields[2] in LOOP_BACKING and not any(f[2] == fields[2] for f in self.lines):
                self.releasing[fields[2]] = self.loop_release
            return 0, ""
        raise AssertionError(f"unexpected command {command!r}")

    def run(self, command, timeout=60):
        status, output = self.try_run(command, timeout)
        if status:
            raise install.PhoneCommandError(command, status, output)
        return output


class ReleaseUserdataTest(unittest.TestCase):
    def release(self, shell):
        with mock.patch.object(install.time, "sleep") as sleep:
            install.release_userdata(shell, USERDATA)
        return sleep

    def test_normal_boot(self):
        shell = FakeMountShell(NORMAL_BOOT_MOUNTINFO)
        sleep = self.release(shell)
        left = {f[4] for f in shell.lines}
        self.assertEqual(left, {"/", "/sys", "/proc", "/dev", "/dev/pts", "/run", "/config", "/mnt/my\\040dir"})
        self.assertEqual(shell.swaps, [])
        self.assertIn("mkdir -p /tmpmnt && mount --move /root/userdata /tmpmnt", shell.commands)
        self.assertEqual(shell.commands[-1], "cat /proc/self/mountinfo")
        # android-rootfs.img is inside ubuntu.img: userdata is free once
        # both loop devices have let go, two table reads after the first try
        self.assertEqual(shell.commands.count("umount /tmpmnt"), 3)
        sleep.assert_not_called()

    def test_slow_loop_release(self):
        shell = FakeMountShell(NORMAL_BOOT_MOUNTINFO, loop_release=4)
        sleep = self.release(shell)
        self.assertNotIn("/tmpmnt", {f[4] for f in shell.lines})
        # Three stalled passes for each of the two loop devices
        self.assertEqual(sleep.call_count, 6)

    def test_early_stop(self):
        # userdata without ubuntu.img: the initramfs stopped with it at /tmpmnt
        shell = FakeMountShell("1 1 0:1 / / rw - rootfs rootfs rw\n"
                               "30 1 8:35 / /tmpmnt rw - ext4 /dev/sda35 rw\n", swaps=())
        self.release(shell)
        self.assertEqual([f[4] for f in shell.lines], ["/"])
        self.assertFalse(any("--move" in c for c in shell.commands))

    def test_nothing_mounted(self):
        shell = FakeMountShell("1 1 0:1 / / rw - rootfs rootfs rw\n", swaps=())
        self.release(shell)
        self.assertEqual(shell.commands, ["cat /proc/swaps", "cat /proc/self/mountinfo"])

    def test_gives_up(self):
        shell = FakeMountShell("1 1 0:1 / / rw - rootfs rootfs rw\n"
                               "30 1 8:35 / /tmpmnt rw - ext4 /dev/sda35 rw\n", swaps=())
        shell._users = lambda: {USERDATA}  # something that never lets go
        with self.assertRaisesRegex(install.InstallError, "Couldn't unmount userdata"):
            self.release(shell)


class DevAccessTest(unittest.TestCase):
    def commands(self, mode):
        shell = mock.Mock()
        install.set_dev_access(shell, "/mnt/userdata", mode)
        return [c.args[0] for c in shell.run.call_args_list]

    def test_modes(self):
        saved = "rm -f /mnt/userdata/system-data/var/lib/usb-moded/usb-moded.ini && "
        self.assertEqual(self.commands(None), [])
        self.assertEqual(self.commands("adb"),
                         [saved + "touch /mnt/userdata/.force-adb && rm -f /mnt/userdata/.force-ssh"])
        self.assertEqual(self.commands("ssh"),
                         [saved + "rm -f /mnt/userdata/.force-adb && touch /mnt/userdata/.force-ssh"])
        # none must also forget the USB mode usb-moded saved on userdata
        self.assertEqual(self.commands("none"),
                         [saved + "rm -f /mnt/userdata/.force-adb && rm -f /mnt/userdata/.force-ssh"])


class FindPhoneTest(unittest.TestCase):
    def find(self, product):
        fastboot = mock.Mock()
        fastboot.devices.return_value = []
        adb = mock.Mock()
        adb.devices.return_value = [("SERIAL1", "device")]
        adb.product.return_value = product
        with mock.patch.object(install.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(install.InstallError):
                install.find_phone(fastboot, adb, assume_yes=True, timeout=0)
        return adb

    def test_refused_adb_reboot_is_a_failure(self):
        adb = install.Adb("/usr/bin/adb")
        refused = subprocess.CompletedProcess([], 0, "", "Failed to write reboot parameter file: Permission denied\n")
        with mock.patch.object(install, "_run_tool", return_value=refused):
            self.assertFalse(adb.reboot_bootloader("SERIAL1"))
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(install, "_run_tool", return_value=ok):
            self.assertTrue(adb.reboot_bootloader("SERIAL1"))

    def test_restarts_only_a_pixel_6a(self):
        self.find("bluejay").reboot_bootloader.assert_called_once_with("SERIAL1")
        self.find("redfin").reboot_bootloader.assert_not_called()


# Command line and messages ###################################################

class CliTest(unittest.TestCase):
    def test_defaults(self):
        args = install.parse_args([])
        self.assertFalse(args.keep_data)
        self.assertIsNone(args.dev_access)
        self.assertFalse(args.yes)
        self.assertIsNone(args.images)
        self.assertFalse(args.verbose)

    def test_options(self):
        args = install.parse_args(["--keep-data", "--dev-access", "adb", "--yes", "--images", "out",
                                   "--verbose"])
        self.assertEqual((args.keep_data, args.dev_access, args.yes, args.images, args.verbose),
                         (True, "adb", True, Path("out"), True))
        with open(os.devnull, "w") as null, contextlib.redirect_stderr(null):
            with self.assertRaises(SystemExit):
                install.parse_args(["--dev-access", "root"])

    def test_missing_images_touch_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        err = io.StringIO()
        with mock.patch.object(install.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(install.main(["--images", tmp, "--yes"]), 1)
        run.assert_not_called()
        self.assertIn("Missing in", err.getvalue())
        self.assertIn("Nothing on the phone was changed.", err.getvalue())

    def test_advice(self):
        state = install.State(keep_data=False)
        self.assertEqual(state.advice(), "Nothing on the phone was changed.")
        state.stage = install.State.INSTALLER
        self.assertIn("Power and Volume Down", state.advice())
        self.assertIn(install.FACTORY_IMAGE_URL, state.advice())
        self.assertNotIn("erased", state.advice())
        state.data_erased = True
        self.assertIn("The phone's data has already been erased.", state.advice())
        self.assertIn("To try again: hold Power", state.advice())
        state.stage = install.State.SYSTEM_INSTALLED
        state.boot_image = "/my images/boot.img"
        self.assertIn("fastboot flash boot_a '/my images/boot.img'", state.advice())
        state.stage = install.State.DONE
        self.assertEqual(state.advice(), "")
        keep = install.State(keep_data=True)
        keep.stage = install.State.INSTALLER
        self.assertIn("unchanged", keep.advice())

    def test_plan(self):
        image = install.SystemImage(Path("system.img.zst"))
        image.size = 5 << 30
        fresh = install.describe_plan("SERIAL", image, keep_data=False, dev_access=None)
        self.assertIn("ERASES ALL DATA", fresh)
        self.assertIn("5.00 GiB", fresh)
        keep = install.describe_plan("SERIAL", image, keep_data=True, dev_access="ssh")
        self.assertNotIn("ERASES", keep)
        self.assertIn(".force-ssh", keep)


# The whole installation against a fake phone ###############################

SERIAL = "26061JEGR01234"


class FakePhone:
    """fastboot, the installer's telnet shell and the nc listener of a phone."""

    def __init__(self, mountinfo, files=(), wrong_digest=False, serial=SERIAL):
        self.serial = serial
        self.mode = "fastboot"
        self.boot_a = "android"
        self.flashed = []
        self.mounts = FakeMountShell(mountinfo, swaps=())
        self.files = dict.fromkeys(files, b"old")
        self.formatted = False
        self.listening = None
        self.stream = None
        self.misc = ""
        self.wrong_digest = wrong_digest

    # fastboot, through subprocess.run
    def run(self, cmd, **_kwargs):
        args = list(cmd[1:])
        if args[:1] == ["-s"]:
            assert args[1] == SERIAL
            args = args[2:]
        out = err = ""
        if args == ["devices"]:
            out = f"{SERIAL}\tfastboot\n" if self.mode == "fastboot" else ""
        elif args[0] == "getvar" and self.mode == "fastboot":
            value = GOOD_PHONE.get(args[1])
            err = f"{args[1]}: {value}\n" if value is not None else f"getvar:{args[1]} FAILED\n"
            err += "Finished. Total time: 0.001s\n"
        elif args[0] == "flash" and self.mode == "fastboot":
            self.flashed.append((args[1], Path(args[2]).name))
            self.boot_a = Path(args[2]).name
        elif args == ["reboot"] and self.mode == "fastboot":
            self.mode = "installer" if self.boot_a == "boot-installer.img" else "ubuntu"
        else:
            raise AssertionError(f"unexpected fastboot call {cmd}")
        return subprocess.CompletedProcess(cmd, 0, out, err)

    def create_connection(self, address, timeout=None):
        host, port = address
        assert host == "192.168.2.15"
        if self.mode != "installer":
            raise ConnectionRefusedError()
        if port == 23:
            return FakeTelnetSocket(self.answer, on_line=self.raw_line)
        if port == 5555 and self.listening:
            self.stream = FakeStreamSocket()
            return self.stream
        raise ConnectionRefusedError()

    def raw_line(self, line):
        if line == "reboot -f" and self.misc == "bootonce-bootloader":
            self.mode = "fastboot"

    def userdata_mounted(self):
        return any(f[2] == USERDATA for f in self.mounts.lines)

    def answer(self, command):
        received = bytes(self.stream.received) if self.stream else b""
        if command == "cat /proc/cmdline":
            return 0, "console=ttySAC0 break=bottom\n"
        if command.startswith("sed -n 's/^androidboot.serialno = "):
            return 0, self.serial + "\n"
        if re.fullmatch(r"command -v \S+ >/dev/null", command):
            return 0, ""
        if command.startswith("d=$(readlink -f /dev/disk/by-partlabel/userdata)"):
            return 0, "8:35\n"
        if command == "mke2fs -F -t ext4 -L userdata /dev/disk/by-partlabel/userdata </dev/null":
            assert not self.userdata_mounted()
            self.formatted = True
            self.files.clear()
            return 0, "mke2fs 1.43.4 (31-Jan-2017)\nWriting superblocks: done\n"
        if command == "mkdir -p /mnt/userdata && mount -t ext4 /dev/disk/by-partlabel/userdata /mnt/userdata":
            self.mounts.lines.append("50 1 8:35 / /mnt/userdata rw - ext4 /dev/sda35 rw".split())
            return 0, ""
        if command.startswith("blkid "):
            return 0, "ext4\n"
        match = re.fullmatch(r"\[ -f (\S+) \]", command)
        if match:
            return (0 if match.group(1) in self.files else 1), ""
        match = re.fullmatch(r"rm -f /run/install-nc.status; \(setsid sh -c 'nc -l -p 5555 > (\S+); "
                             r"echo \$\? > /run/install-nc.status' </dev/null >/dev/null 2>&1 &\)", command)
        if match:
            self.listening = match.group(1)
            return 0, ""
        if command.startswith("df -P -k "):
            return 0, ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                       f"/dev/sda35 110000000 100 109999900 1% {command.split()[-1]}\n")
        if command.startswith("i=0; while [ ! -s /run/install-nc.status ]"):
            return 0, "0\n" if self.stream and self.stream.shut else ""
        if command == f"ls -l {self.listening}":
            return 0, f"-rw-r--r--    1 0        0        {len(received)} Oct 15 12:00 {self.listening}\n"
        if command == f"sha256sum {self.listening}":
            digest = hashlib.sha256(received + (b"x" if self.wrong_digest else b"")).hexdigest()
            return 0, f"{digest}  {self.listening}\n"
        match = re.fullmatch(r"mv -f (\S+) (\S+)", command)
        if match:
            assert match.group(1) == self.listening
            self.files[match.group(2)] = received
            return 0, ""
        if re.fullmatch(r"(touch|rm -f) \S+( && (touch|rm -f) \S+)+", command):
            for verb, path in re.findall(r"(touch|rm -f) (\S+)", command):
                if verb == "touch":
                    self.files[path] = b""
                else:
                    self.files.pop(path, None)
            return 0, ""
        if command.startswith("rm -f "):
            self.files.pop(command[len("rm -f "):], None)
            return 0, ""
        if command == "sync":
            return 0, ""
        if command == "printf bootonce-bootloader | dd of=/dev/disk/by-partlabel/misc bs=32 count=1 conv=notrunc,sync":
            self.misc = "bootonce-bootloader"
            return 0, "0+1 records in\n1+0 records out\n"
        if command == "dd if=/dev/disk/by-partlabel/misc bs=32 count=1 2>/dev/null | tr -d '\\000'":
            return 0, self.misc
        return self.mounts.try_run(command)


class InstallFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.raw = os.urandom(3 * 4096)
        (self.tmp / "boot.img").write_bytes(boot_header(""))
        (self.tmp / "boot-installer.img").write_bytes(boot_header("break=bottom"))
        (self.tmp / "system.img").write_bytes(sparse_image(4096, [
            (install.CHUNK_RAW, 3, self.raw), (install.CHUNK_DONT_CARE, 5, b"")]))
        self.expected = self.raw + bytes(5 * 4096)

    def install(self, phone, *args):
        out, err = io.StringIO(), io.StringIO()
        which = {"fastboot": "/usr/bin/fastboot"}.get
        with mock.patch.object(install.subprocess, "run", phone.run), \
                mock.patch.object(install.socket, "create_connection", phone.create_connection), \
                mock.patch.object(install.shutil, "which", which), \
                mock.patch.object(install.time, "sleep"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = install.main(["--images", str(self.tmp), "--yes"] + list(args))
        return status, out.getvalue(), err.getvalue()

    def test_fresh_install(self):
        phone = FakePhone(NORMAL_BOOT_MOUNTINFO, files=["/root/userdata/ubuntu.img"])
        status, out, err = self.install(phone, "--dev-access", "adb")
        self.assertEqual(status, 0, err)
        self.assertEqual(phone.flashed, [("boot_a", "boot-installer.img"), ("boot_a", "boot.img")])
        self.assertEqual(phone.mode, "ubuntu")
        self.assertTrue(phone.formatted)
        self.assertEqual(phone.files, {"/mnt/userdata/ubuntu.img": self.expected,
                                       "/mnt/userdata/.force-adb": b""})
        self.assertFalse(phone.userdata_mounted())
        self.assertIn("ERASES ALL DATA", out)
        self.assertIn("Done.", out)

    def test_keep_data(self):
        phone = FakePhone(NORMAL_BOOT_MOUNTINFO, files=["/root/userdata/ubuntu.img",
                                                        "/root/userdata/.force-ssh"])
        status, out, err = self.install(phone, "--keep-data")
        self.assertEqual(status, 0, err)
        self.assertFalse(phone.formatted)
        self.assertEqual(phone.files, {"/root/userdata/ubuntu.img": self.expected,
                                       "/root/userdata/.force-ssh": b"old"})
        self.assertEqual(phone.mode, "ubuntu")

    def test_installer_on_another_phone(self):
        phone = FakePhone(NORMAL_BOOT_MOUNTINFO, files=["/root/userdata/ubuntu.img"],
                          serial="OTHERPHONE")
        status, _out, err = self.install(phone)
        self.assertEqual(status, 1)
        self.assertIn("runs on the phone OTHERPHONE", err)
        self.assertFalse(phone.formatted)

    def test_keep_data_without_install(self):
        phone = FakePhone("1 1 0:1 / / rw - rootfs rootfs rw\n")
        status, _out, err = self.install(phone, "--keep-data")
        self.assertEqual(status, 1)
        self.assertIn("no ubuntu.img", err)
        self.assertIn("Your data and the installed system are unchanged", err)
        self.assertEqual(phone.flashed, [("boot_a", "boot-installer.img")])

    def test_bad_copy(self):
        phone = FakePhone(NORMAL_BOOT_MOUNTINFO, wrong_digest=True)
        status, _out, err = self.install(phone)
        self.assertEqual(status, 1)
        self.assertIn("doesn't match", err)
        self.assertIn("data has already been erased", err)
        self.assertIn("the phone has 32768 bytes", err)
        self.assertIn("Power and Volume Down", err)
        self.assertNotIn("/mnt/userdata/ubuntu.img", phone.files)
        self.assertEqual(phone.flashed, [("boot_a", "boot-installer.img")])


if __name__ == "__main__":
    unittest.main()
