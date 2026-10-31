#!/usr/bin/env python3
import argparse
import hashlib
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

SOURCE_SHA256 = "edd24c9fa4437d0ab81f1f4a6f91410602097b455d40ce66d119a8a5d5c3bb6a"
FLASH_SIZE = 0x2000000
OLD_GLOBAL_FMH = 0x3FF0000
NEW_GLOBAL_FMH = 0x1FF0000
ERASE_BLOCK_SIZE = 0x10000
BOOT_FMH = 0x8FF00
BOOT_PAYLOAD_SIZE = 0x85571
BOOT_SCAN_BOUND = 0x57AEC
ROOT_FMH = 0x2B0000
ROOT_PAYLOAD = 0x2C0000
NEXT_FMH = 0x14C0000
NORMAL_FMH_HEADERS = (
    BOOT_FMH,
    0xA0000,
    0x1A0000,
    0x2A0000,
    ROOT_FMH,
    0x14C0000,
    0x1810000,
    0x1D10000,
    0x1D40000,
)


def image_crc(image, global_fmh):
    crc = zlib.crc32(image[:global_fmh + 0x17])
    crc = zlib.crc32(image[global_fmh + 0x18:global_fmh + 0x32], crc)
    crc = zlib.crc32(image[global_fmh + 0x36:global_fmh + ERASE_BLOCK_SIZE], crc)
    return crc & 0xFFFFFFFF


def finish_fmh(image, offset):
    image[offset + 0x17] = 0
    image[offset + 0x17] = (-sum(image[offset:offset + 64])) & 0xFF


def set_module_crc(image, offset, payload, size):
    struct.pack_into("<I", image, offset + 0x32, zlib.crc32(image[payload:payload + size]) & 0xFFFFFFFF)
    finish_fmh(image, offset)


def replace_text(path, old, new, expected_count):
    metadata = path.stat()
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected_count:
        raise ValueError(f"{path}: expected {expected_count} occurrences of {old!r}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def patch_helper(path):
    metadata = path.stat()
    data = bytearray(path.read_bytes())
    if data[0x1988:0x198C] != bytes.fromhex("0123a0e3"):
        raise ValueError(f"Unexpected helper.ko instructions in {path}")
    data[0x1988:0x198C] = bytes.fromhex("0224a0e3")
    path.write_bytes(data)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def patch_hpm(path):
    metadata = path.stat()
    data = bytearray(path.read_bytes())
    entry = 0xDCF8
    if struct.unpack_from("<4I", data, entry) != (0, 0xA0000, 0x3F60000, 0x3F6000C):
        raise ValueError(f"Unexpected HPM table in {path}")
    if data[entry + 16:entry + 19] != b"APP":
        raise ValueError(f"Unexpected HPM APP descriptor in {path}")
    struct.pack_into("<I", data, entry + 8, 0x1F60000)
    struct.pack_into("<I", data, entry + 12, 0x1F6000C)
    path.write_bytes(data)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def validate(image):
    if len(image) != FLASH_SIZE:
        raise ValueError("Output is not 32 MiB")
    for offset in NORMAL_FMH_HEADERS:
        header = image[offset:offset + 64]
        if header[:8] != b"$MODULE$" or sum(header) & 0xFF:
            raise ValueError(f"Invalid FMH at 0x{offset:x}")
        if struct.unpack_from("<I", header, 16)[0] != offset:
            raise ValueError(f"Wrong FMH location at 0x{offset:x}")
        payload, size, stored_crc = struct.unpack_from("<III", header, 36)[0], struct.unpack_from("<I", header, 40)[0], struct.unpack_from("<I", header, 50)[0]
        if payload + size > len(image):
            raise ValueError(f"Module at 0x{offset:x} exceeds 32 MiB")
        if (zlib.crc32(image[payload:payload + size]) & 0xFFFFFFFF) != stored_crc:
            raise ValueError(f"Module CRC mismatch at 0x{offset:x}")
    if struct.unpack_from("<I", image, BOOT_SCAN_BOUND)[0] != 0x1FF:
        raise ValueError("U-Boot scan bound was not patched")
    if image[NEW_GLOBAL_FMH:NEW_GLOBAL_FMH + 8] != b"$MODULE$":
        raise ValueError("Global FMH is missing")
    if struct.unpack_from("<I", image, NEW_GLOBAL_FMH + 16)[0] != NEW_GLOBAL_FMH:
        raise ValueError("Global FMH location is wrong")
    if struct.unpack_from("<I", image, NEW_GLOBAL_FMH + 36)[0] != NEW_GLOBAL_FMH + 0x40:
        raise ValueError("Global module location is wrong")
    if sum(image[NEW_GLOBAL_FMH:NEW_GLOBAL_FMH + 64]) & 0xFF:
        raise ValueError("Global FMH header checksum is wrong")
    if image_crc(image, NEW_GLOBAL_FMH) != struct.unpack_from("<I", image, NEW_GLOBAL_FMH + 0x32)[0]:
        raise ValueError("Whole-image CRC is wrong")


def main():
    parser = argparse.ArgumentParser(description="Patch Tyan S8030 BMC v4.07 for a 32 MiB flash chip")
    parser.add_argument("input", nargs="?", default="8030010407.ima")
    parser.add_argument("output", nargs="?", default="8030010407-32m.ima")
    parser.add_argument("--unsquashfs", default="unsquashfs")
    parser.add_argument("--mksquashfs", default="mksquashfs")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    source_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if source_path == output_path:
        raise ValueError("Input and output must differ")
    if output_path.exists() and not args.force:
        raise FileExistsError(f"{output_path} exists; use --force to replace it")
    source = source_path.read_bytes()
    if len(source) != 0x4000000 or hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("Input is not the expected unmodified S8030 v4.07 image")
    if image_crc(source, OLD_GLOBAL_FMH) != struct.unpack_from("<I", source, OLD_GLOBAL_FMH + 0x32)[0]:
        raise ValueError("Source whole-image CRC is invalid")
    if source[FLASH_SIZE:OLD_GLOBAL_FMH] != b"\xFF" * (OLD_GLOBAL_FMH - FLASH_SIZE):
        raise ValueError("Unexpected data above 32 MiB")
    if source[NEW_GLOBAL_FMH:NEW_GLOBAL_FMH + ERASE_BLOCK_SIZE] != b"\xFF" * ERASE_BLOCK_SIZE:
        raise ValueError("The 32 MiB metadata destination is not erased")
    with tempfile.TemporaryDirectory(prefix=".bmc32-", dir=Path.cwd()) as temporary:
        work = Path(temporary)
        rootfs = work / "rootfs"
        subprocess.run([args.unsquashfs, "-d", str(rootfs), "-o", str(ROOT_PAYLOAD), str(source_path)], check=True)
        for relative in ("etc/core_macros", "info/8030.PRJ"):
            replace_text(rootfs / relative, "FLASH_SIZE=0x4000000", "FLASH_SIZE=0x2000000", 2)
        patch_helper(rootfs / "lib/modules/generic/misc/helper.ko")
        patch_hpm(rootfs / "usr/local/lib/libipmihpm.so.6.22.11")
        squashfs = work / "rootfs.sqfs"
        mkfs_time = struct.unpack_from("<I", source, ROOT_PAYLOAD + 8)[0]
        subprocess.run([
            args.mksquashfs,
            str(rootfs),
            str(squashfs),
            "-noappend",
            "-comp",
            "xz",
            "-b",
            "128K",
            "-mkfs-time",
            str(mkfs_time),
            "-exports",
            "-processors",
            "4",
        ], check=True)
        root_payload = squashfs.read_bytes()
    if len(root_payload) > NEXT_FMH - ROOT_PAYLOAD:
        raise ValueError("Patched root filesystem exceeds its allocated region")
    image = bytearray(source[:FLASH_SIZE])
    image[ROOT_PAYLOAD:NEXT_FMH] = b"\xFF" * (NEXT_FMH - ROOT_PAYLOAD)
    image[ROOT_PAYLOAD:ROOT_PAYLOAD + len(root_payload)] = root_payload
    struct.pack_into("<I", image, ROOT_FMH + 0x28, len(root_payload))
    set_module_crc(image, ROOT_FMH, ROOT_PAYLOAD, len(root_payload))
    if struct.unpack_from("<I", image, BOOT_SCAN_BOUND)[0] != 0x3FF:
        raise ValueError("Unexpected U-Boot scan bound")
    struct.pack_into("<I", image, BOOT_SCAN_BOUND, 0x1FF)
    set_module_crc(image, BOOT_FMH, 0, BOOT_PAYLOAD_SIZE)
    global_block = bytearray(source[OLD_GLOBAL_FMH:OLD_GLOBAL_FMH + ERASE_BLOCK_SIZE])
    struct.pack_into("<I", global_block, 0x10, NEW_GLOBAL_FMH)
    struct.pack_into("<I", global_block, 0x24, NEW_GLOBAL_FMH + 0x40)
    global_block[0x17] = 0
    global_block[0x32:0x36] = b"\0" * 4
    image[NEW_GLOBAL_FMH:NEW_GLOBAL_FMH + ERASE_BLOCK_SIZE] = global_block
    struct.pack_into("<I", image, NEW_GLOBAL_FMH + 0x32, image_crc(image, NEW_GLOBAL_FMH))
    finish_fmh(image, NEW_GLOBAL_FMH)
    validate(image)
    with output_path.open("wb" if args.force else "xb") as output:
        output.write(image)
    print(f"Wrote {output_path} ({len(image)} bytes)")
    print(f"Root filesystem: {len(root_payload)} bytes")
    print(f"Whole-image CRC: 0x{image_crc(image, NEW_GLOBAL_FMH):08x}")
    print(f"SHA-256: {hashlib.sha256(image).hexdigest()}")


if __name__ == "__main__":
    main()
