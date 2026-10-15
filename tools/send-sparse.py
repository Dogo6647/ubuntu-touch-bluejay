#!/usr/bin/env python3
"""Expand an Android sparse image and stream the raw bytes to host:port over TCP.

Prints the SHA-256 of the raw stream and its size, so the receiver can check it.
Used to copy system.img to /userdata/ubuntu.img from the Halium initramfs
debug shell (see the README).

Usage: send-sparse.py IMAGE HOST PORT
"""
import hashlib
import socket
import struct
import sys
import time

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
ZEROS = bytes(1 << 20)


def main():
    path, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sha = hashlib.sha256()
    sent = 0
    start = time.time()
    with open(path, "rb") as f, socket.create_connection((host, port)) as sock:
        magic, major, minor, file_hdr_sz, chunk_hdr_sz, blk_sz, total_blks, total_chunks, _ = \
            struct.unpack("<IHHHHIIII", f.read(28))
        if magic != SPARSE_MAGIC or major != 1:
            sys.exit("not an Android sparse image (v1)")
        f.seek(file_hdr_sz)

        def out(data):
            nonlocal sent
            sha.update(data)
            sock.sendall(data)
            sent += len(data)

        def out_repeat(pattern_block, nbytes):
            # pattern_block is one block worth of data
            big = pattern_block * max(1, (1 << 20) // len(pattern_block))
            while nbytes > 0:
                n = min(nbytes, len(big))
                out(big[:n])
                nbytes -= n

        for i in range(total_chunks):
            ctype, _, nblks, total_sz = struct.unpack("<HHII", f.read(12))
            f.seek(chunk_hdr_sz - 12, 1)
            data_sz = total_sz - chunk_hdr_sz
            nbytes = nblks * blk_sz
            if ctype == CHUNK_RAW:
                remaining = data_sz
                while remaining > 0:
                    buf = f.read(min(remaining, 8 << 20))
                    if not buf:
                        sys.exit("truncated image")
                    out(buf)
                    remaining -= len(buf)
            elif ctype == CHUNK_FILL:
                fill = f.read(4)
                out_repeat(fill * (blk_sz // 4), nbytes)
            elif ctype == CHUNK_DONT_CARE:
                out_repeat(ZEROS[:blk_sz], nbytes)
            elif ctype == CHUNK_CRC32:
                f.seek(data_sz, 1)
            else:
                sys.exit(f"unknown chunk type {ctype:#x}")
            if i % 200 == 0:
                rate = sent / max(time.time() - start, 0.001) / 1e6
                print(f"{sent / (blk_sz * total_blks) * 100:5.1f}%  {rate:.1f} MB/s", flush=True)
        sock.shutdown(socket.SHUT_WR)
        # wait for the receiver to close
        sock.settimeout(120)
        while sock.recv(4096):
            pass
    if sent != blk_sz * total_blks:
        sys.exit(f"size mismatch: sent {sent}, expected {blk_sz * total_blks}")
    print(f"done: {sent} bytes in {time.time() - start:.0f}s")
    print(f"sha256 {sha.hexdigest()}")


if __name__ == "__main__":
    main()
