#!/usr/bin/env python3
import argparse
import hashlib
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

FIRMWARES = {
    "edd24c9fa4437d0ab81f1f4a6f91410602097b455d40ce66d119a8a5d5c3bb6a": "v4.07",
    "a4849b7d577e9d1c51a0f3e6dfa1b4e1637503182421df7a44f602db2a2e0018": "v4.10",
    "160ce96489bc0347279241d41f4bf84146d7f17c9e0516d06162fed4e57ca98b": "v4.11",
    "b3327ffb11b65e1382d0bace111adc8dcd95c2b33bdca45c8f674552d283bc41": "v5.00",
    "445e269d612f788e30a9194a625b2f2ed20cb4511d133dc859f720358c67a67a": "v6.02",
}
FLASH_SIZE = 0x2000000
# Reject added, removed, or duplicated module types instead of patching an unfamiliar layout.
EXPECTED_MODULES = {
    "boot": 1,
    "conf": 2,
    "dtb": 1,
    "root": 1,
    "osimage": 1,
    "www": 1,
    "testapps": 1,
    "bootlogo": 1,
    "ast2500e": 1,
}


def image_crc(image, global_fmh, erase_block_size):
    # The whole-image CRC covers the image start through the end of the global
    # metadata erase block. Skip the header-checksum byte at +0x17 and the
    # whole-image CRC field at +0x32..+0x35; all other bytes participate.
    crc = zlib.crc32(image[:global_fmh + 0x17])
    crc = zlib.crc32(image[global_fmh + 0x18:global_fmh + 0x32], crc)
    crc = zlib.crc32(image[global_fmh + 0x36:global_fmh + erase_block_size], crc)
    return crc & 0xFFFFFFFF


def finish_fmh(image, offset):
    # FMH headers are valid when all 64 bytes sum to zero modulo 256. Clear the
    # checksum byte before calculating its two's-complement value.
    old_checksum = image[offset + 0x17]
    image[offset + 0x17] = 0
    image[offset + 0x17] = (-sum(image[offset:offset + 64])) & 0xFF
    print(f"FMH 0x{offset:x}: header checksum 0x{old_checksum:02x} -> 0x{image[offset + 0x17]:02x}")


def set_module_crc(image, offset, payload, size):
    # Store the payload CRC at +0x32, then update the FMH header checksum because
    # those four CRC bytes also change the header's byte sum.
    old_crc = struct.unpack_from("<I", image, offset + 0x32)[0]
    crc = zlib.crc32(image[payload:payload + size]) & 0xFFFFFFFF
    struct.pack_into("<I", image, offset + 0x32, crc)
    print(f"FMH 0x{offset:x}: payload CRC 0x{old_crc:08x} -> 0x{crc:08x} at +0x32")
    finish_fmh(image, offset)


def find_modules(image):
    modules = []
    position = 0
    while True:
        offset = image.find(b"$MODULE$", position)
        if offset < 0:
            break
        position = offset + 1
        if offset + 64 > len(image):
            continue
        header = image[offset:offset + 64]
        # A real header points to itself at +16 and ends with the 0xaa55 signature.
        # These checks reject coincidental "$MODULE$" strings inside module payloads.
        if struct.unpack_from("<I", header, 16)[0] != offset or header[62:64] != b"\xaaU":
            continue
        if sum(header) & 0xFF:
            raise ValueError(f"Invalid FMH header checksum at 0x{offset:x}")
        # The name is at +24; payload offset and payload size are at +36 and +40.
        name = header[24:32].split(b"\0")[0].decode("ascii")
        payload, size = struct.unpack_from("<II", header, 36)
        if payload + size > len(image):
            raise ValueError(f"Module {name} at 0x{offset:x} exceeds the image")
        modules.append({
            "name": name,
            "offset": offset,
            "payload": payload,
            "size": size,
        })
    return modules


def validate_module_set(modules):
    counts = {name: 0 for name in EXPECTED_MODULES}
    for module in modules:
        if module["name"] not in counts:
            raise ValueError(f"Unexpected FMH module {module['name']}")
        counts[module["name"]] += 1
    if counts != EXPECTED_MODULES:
        raise ValueError(f"Unexpected FMH module set: {counts}")


def get_module(modules, name):
    # Singular modules such as boot and root must be unambiguous; conf is handled separately.
    matches = [module for module in modules if module["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one {name} module, found {len(matches)}")
    return matches[0]


def find_boot_scan_bound(image, boot_size, expected):
    # The bound is a data word in a U-Boot table, not an instruction or a fixed address.
    # Identify the table through several independent pointer relationships around it.
    matches = []
    for offset in range(12, boot_size - 48, 4):
        if struct.unpack_from("<I", image, offset)[0] != expected:
            continue

        def word(relative):
            return struct.unpack_from("<I", image, offset + relative)[0]

        # Every candidate neighbor must point within the U-Boot payload. This alone
        # rejects most literal 0x3ff or 0x1ff values elsewhere in the image.
        pointers = [word(relative) for relative in (-12, -8, -4, 4, 8, 12, 16, 20, 24, 28)]
        if any(pointer == 0 or pointer >= boot_size for pointer in pointers):
            continue
        # The three pointers before the bound have fixed +0x30 and +0x16 deltas.
        if word(-12) + 0x30 != word(-8) or word(-8) + 0x16 != word(-4):
            continue
        # The pointer triplet after the bound has fixed +4 and +9 deltas.
        if word(8) + 4 != word(12) or word(12) + 9 != word(16):
            continue
        # A later pointer pair has a fixed +0x1b delta. All supported U-Boot
        # builds have exactly one table satisfying every relationship.
        if word(24) + 0x1B != word(28):
            continue
        matches.append(offset)
    if len(matches) != 1:
        raise ValueError(f"Expected one U-Boot scan-bound table, found {len(matches)}")
    return matches[0]


def flash_geometry(image, scan_bound):
    # U-Boot stores the last sector index, so sector count is index + 1.
    # Derive the sector size from the actual image length and that count.
    sector_count = struct.unpack_from("<I", image, scan_bound)[0] + 1
    if sector_count == 0 or len(image) % sector_count:
        raise ValueError("U-Boot sector count does not divide the image size")
    erase_block_size = len(image) // sector_count
    if erase_block_size == 0 or FLASH_SIZE % erase_block_size:
        raise ValueError("Derived erase block size is incompatible with 32 MiB flash")
    output_last_sector = FLASH_SIZE // erase_block_size - 1
    return erase_block_size, output_last_sector


def find_boot_unlock(image, boot, flash_size):
    matches = []
    base = boot["payload"]
    for offset in range(base, base + boot["size"] - 20, 4):
        instruction = struct.unpack_from("<I", image, offset)[0]
        if instruction & 0x0FFF0000 != 0x03A00000:
            continue
        rotation = ((instruction >> 8) & 0xF) * 2
        if rotate_right(instruction & 0xFF, rotation) != flash_size:
            continue
        # The recovery unlock sequence loads a 64-bit length onto the stack,
        # clears the 64-bit offset in r2:r3, then calls flash_unlock through r1.
        following = struct.unpack_from("<5I", image, offset + 4)
        if following != (0xE3A03000, 0xE88D000C, 0xE3A02000, 0xE3A03000, 0xE12FFF31):
            continue
        matches.append((offset, instruction))
    if len(matches) != 1:
        raise ValueError(f"Expected one U-Boot recovery unlock instruction, found {len(matches)}")
    return matches[0]


def patch_boot_unlock(image, boot, input_flash_size, output_flash_size):
    offset, instruction = find_boot_unlock(image, boot, input_flash_size)
    new_instruction = (instruction & 0xFFFFF000) | encode_arm_immediate(output_flash_size)
    struct.pack_into("<I", image, offset, new_instruction)
    print(f"U-Boot recovery unlock 0x{offset:x}: 0x{instruction:08x} -> 0x{new_instruction:08x} (length 0x{input_flash_size:x} -> 0x{output_flash_size:x})")


def replace_text(path, old, new, expected_count):
    metadata = path.stat()
    text = path.read_text(encoding="utf-8")
    # Require the exact number of settings found during analysis so partial or
    # unexpectedly changed configuration files are never rewritten.
    count = text.count(old)
    if count != expected_count:
        raise ValueError(f"{path}: expected {expected_count} occurrences of {old!r}, found {count}")
    position = 0
    for _ in range(count):
        offset = text.find(old, position)
        byte_offset = len(text[:offset].encode("utf-8"))
        print(f"{path.name}: text offset 0x{byte_offset:x}: {old} -> {new}")
        position = offset + len(old)
    path.write_text(text.replace(old, new), encoding="utf-8")
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def rotate_right(value, rotation):
    if rotation == 0:
        return value
    return ((value >> rotation) | (value << (32 - rotation))) & 0xFFFFFFFF


def encode_arm_immediate(value):
    # ARM represents an immediate as an 8-bit value rotated right by an even
    # number of bits. Search every encoding and use the first exact match.
    for rotation in range(0, 32, 2):
        for immediate in range(256):
            if rotate_right(immediate, rotation) == value:
                return ((rotation // 2) << 8) | immediate
    raise ValueError(f"Cannot encode 0x{value:x} as an ARM rotated immediate")


def patch_helper(path, input_flash_size, output_flash_size):
    metadata = path.stat()
    data = bytearray(path.read_bytes())
    matches = []
    for offset in range(0, len(data) - 3, 4):
        instruction = struct.unpack_from("<I", data, offset)[0]
        # Match MOV (immediate) with S=0 and Rn=0. The mask deliberately
        # excludes the condition code, destination register, rotation, and imm8.
        if instruction & 0x0FFF0000 != 0x03A00000:
            continue
        # Bits 11:8 select an even right rotation for the 8-bit immediate.
        # Compare the decoded value, not a hard-coded instruction encoding.
        rotation = ((instruction >> 8) & 0xF) * 2
        if rotate_right(instruction & 0xFF, rotation) == input_flash_size:
            matches.append((offset, instruction))
    if len(matches) != 1:
        raise ValueError(f"Expected one helper MOV instruction matching the input flash size, found {len(matches)}")
    offset, instruction = matches[0]
    encoded = encode_arm_immediate(output_flash_size)
    # Preserve the condition code, opcode, and destination register. Replace
    # only the rotation and imm8 fields containing the output flash size.
    new_instruction = (instruction & 0xFFFFF000) | encoded
    struct.pack_into("<I", data, offset, new_instruction)
    print(f"{path.name}: instruction offset 0x{offset:x}: 0x{instruction:08x} -> 0x{new_instruction:08x} (flash size 0x{input_flash_size:x} -> 0x{output_flash_size:x})")
    path.write_bytes(data)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def patch_hpm(path, input_flash_size, output_flash_size):
    metadata = path.stat()
    data = bytearray(path.read_bytes())
    matches = []
    position = 0
    while True:
        app = data.find(b"APP", position)
        if app < 0:
            break
        position = app + 1
        # The APP descriptor consists of four 32-bit words immediately before
        # the name: component ID, reserved area, APP size, and allocation size.
        entry = app - 16
        if entry < 0:
            continue
        component, reserved, size, allocation = struct.unpack_from("<4I", data, entry)
        if component != 0 or reserved == 0 or size == 0 or allocation < size:
            continue
        # The stock reserved area plus APP size must equal the complete input
        # image. This arithmetic rejects unrelated occurrences of "APP".
        if reserved + size == input_flash_size:
            matches.append((entry, reserved, allocation - size))
    if len(matches) != 1:
        raise ValueError(f"Expected one HPM APP entry matching the input flash size, found {len(matches)}")
    entry, reserved, allocation_adjustment = matches[0]
    # Preserve the input's allocation overhead rather than assuming a constant.
    old_size = struct.unpack_from("<I", data, entry + 8)[0]
    old_allocation = struct.unpack_from("<I", data, entry + 12)[0]
    new_size = output_flash_size - reserved
    new_allocation = new_size + allocation_adjustment
    if new_size <= 0:
        raise ValueError("HPM reserved area exceeds the output flash size")
    struct.pack_into("<I", data, entry + 8, new_size)
    struct.pack_into("<I", data, entry + 12, new_allocation)
    print(f"{path.name}: APP entry 0x{entry:x}: size 0x{old_size:x} -> 0x{new_size:x} at +0x8, allocation 0x{old_allocation:x} -> 0x{new_allocation:x} at +0xc")
    path.write_bytes(data)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))


def validate(image, erase_block_size, output_last_sector):
    if len(image) != FLASH_SIZE:
        raise ValueError("Output is not 32 MiB")
    modules = find_modules(image)
    validate_module_set(modules)
    global_fmh = get_module(modules, "ast2500e")
    for module in modules:
        # ast2500e stores the whole-image CRC rather than a payload CRC, so it
        # is checked separately below instead of being treated as a normal module.
        if module["name"] == "ast2500e":
            continue
        offset = module["offset"]
        stored_crc = struct.unpack_from("<I", image, offset + 50)[0]
        payload = module["payload"]
        size = module["size"]
        if (zlib.crc32(image[payload:payload + size]) & 0xFFFFFFFF) != stored_crc:
            raise ValueError(f"Module CRC mismatch at 0x{offset:x}")
    boot = get_module(modules, "boot")
    find_boot_scan_bound(image, boot["size"], output_last_sector)
    find_boot_unlock(image, boot, FLASH_SIZE)
    # Relocated global metadata must occupy the final erase block, with its
    # payload immediately following the 64-byte FMH header.
    expected_global = FLASH_SIZE - erase_block_size
    if global_fmh["offset"] != expected_global:
        raise ValueError("Global FMH is not in the final 32 MiB erase block")
    if global_fmh["payload"] != expected_global + 0x40:
        raise ValueError("Global module location is wrong")
    stored_crc = struct.unpack_from("<I", image, expected_global + 0x32)[0]
    if image_crc(image, expected_global, erase_block_size) != stored_crc:
        raise ValueError("Whole-image CRC is wrong")


def main():
    parser = argparse.ArgumentParser(description="Patch supported Tyan S8030 BMC firmware for a 32 MiB flash chip")
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    source_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if source_path == output_path:
        raise ValueError("Input and output must be different files")
    source = source_path.read_bytes()
    if len(source) != 2 * FLASH_SIZE:
        raise ValueError("Input is not a 64 MiB S8030 firmware image")
    # Exact stock hashes are the first guard against applying the analyzed
    # changes to a modified image or an untested release.
    source_hash = hashlib.sha256(source).hexdigest()
    if source_hash not in FIRMWARES:
        supported = ", ".join(FIRMWARES.values())
        raise ValueError(f"Unsupported or modified firmware image; supported versions: {supported}")
    print(f"Input firmware: {FIRMWARES[source_hash]}")

    modules = find_modules(source)
    validate_module_set(modules)
    boot = get_module(modules, "boot")
    root = get_module(modules, "root")
    global_fmh = get_module(modules, "ast2500e")
    old_global = global_fmh["offset"]
    scan_bound = find_boot_scan_bound(source, boot["size"], 0x3FF)
    erase_block_size, output_last_sector = flash_geometry(source, scan_bound)
    new_global = FLASH_SIZE - erase_block_size
    print(f"Detected U-Boot scan bound at 0x{scan_bound:x}")
    # The boot module is the only supported module whose payload begins at zero.
    if boot["payload"] != 0:
        raise ValueError("Unexpected U-Boot payload location")
    # A little-endian SquashFS superblock starts with "hsqs". This distinguishes
    # the root filesystem from the web filesystem and other module payloads.
    if source[root["payload"]:root["payload"] + 4] != b"hsqs":
        raise ValueError("Root module is not a little-endian SquashFS filesystem")
    if old_global + erase_block_size != len(source):
        raise ValueError("Global FMH is not in the final erase block")
    if image_crc(source, old_global, erase_block_size) != struct.unpack_from("<I", source, old_global + 0x32)[0]:
        raise ValueError("Source whole-image CRC is invalid")
    # Everything discarded above 32 MiB must be erased padding, not a payload.
    if source[FLASH_SIZE:old_global] != b"\xFF" * (old_global - FLASH_SIZE):
        raise ValueError("Unexpected data above 32 MiB")
    # The relocation destination must also be free before overwriting it.
    if source[new_global:new_global + erase_block_size] != b"\xFF" * erase_block_size:
        raise ValueError("The 32 MiB metadata destination is not erased")
    # The next FMH header bounds the region available for the rebuilt rootfs.
    root_end = min(module["offset"] for module in modules if module["offset"] > root["offset"])

    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        rootfs = work / "rootfs"
        subprocess.run([
            "unsquashfs",
            "-d",
            str(rootfs),
            "-o",
            str(root["payload"]),
            str(source_path),
        ], check=True)
        for relative in ("etc/core_macros", "info/8030.PRJ"):
            replace_text(rootfs / relative, "FLASH_SIZE=0x4000000", "FLASH_SIZE=0x2000000", 2)
        patch_helper(rootfs / "lib/modules/generic/misc/helper.ko", len(source), FLASH_SIZE)
        patch_hpm(rootfs / "usr/local/lib/libipmihpm.so.6.22.11", len(source), FLASH_SIZE)
        squashfs = work / "rootfs.sqfs"
        mkfs_time = struct.unpack_from("<I", source, root["payload"] + 8)[0]
        subprocess.run([
            "mksquashfs",
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

    if len(root_payload) > root_end - root["payload"]:
        raise ValueError("Patched root filesystem exceeds its allocated region")
    image = bytearray(source[:FLASH_SIZE])
    print(f"Image size: 0x{len(source):x} -> 0x{len(image):x}")
    image[root["payload"]:root_end] = b"\xFF" * (root_end - root["payload"])
    image[root["payload"]:root["payload"] + len(root_payload)] = root_payload
    # The rebuilt SquashFS can have a different size. Update the actual payload
    # size at +0x28 before regenerating the root payload CRC and FMH checksum.
    old_root_size = struct.unpack_from("<I", image, root["offset"] + 0x28)[0]
    struct.pack_into("<I", image, root["offset"] + 0x28, len(root_payload))
    print(f"root payload 0x{root['payload']:x}: size 0x{old_root_size:x} -> 0x{len(root_payload):x}; FMH size field 0x{root['offset'] + 0x28:x}")
    set_module_crc(image, root["offset"], root["payload"], len(root_payload))
    # Changing the scanner bound also changes U-Boot's payload CRC.
    old_scan_bound = struct.unpack_from("<I", image, scan_bound)[0]
    struct.pack_into("<I", image, scan_bound, output_last_sector)
    print(f"U-Boot scan bound 0x{scan_bound:x}: 0x{old_scan_bound:x} -> 0x{output_last_sector:x}")
    patch_boot_unlock(image, boot, len(source), FLASH_SIZE)
    set_module_crc(image, boot["offset"], boot["payload"], boot["size"])

    global_block = bytearray(source[old_global:old_global + erase_block_size])
    print(f"Global FMH relocation: header 0x{old_global:x} -> 0x{new_global:x}, payload 0x{old_global + 0x40:x} -> 0x{new_global + 0x40:x}")
    struct.pack_into("<I", global_block, 0x10, new_global)
    struct.pack_into("<I", global_block, 0x24, new_global + 0x40)
    # Clear the old whole-image CRC before relocating the block.
    old_image_crc = struct.unpack_from("<I", global_block, 0x32)[0]
    global_block[0x32:0x36] = b"\0" * 4
    image[new_global:new_global + erase_block_size] = global_block
    new_image_crc = image_crc(image, new_global, erase_block_size)
    struct.pack_into("<I", image, new_global + 0x32, new_image_crc)
    print(f"Whole-image CRC 0x{new_global + 0x32:x}: 0x{old_image_crc:08x} -> 0x{new_image_crc:08x}")
    # The new whole-image CRC changes the global FMH header, so finish with its checksum.
    finish_fmh(image, new_global)

    validate(image, erase_block_size, output_last_sector)
    with output_path.open("wb") as output:
        output.write(image)
    print(f"Wrote {output_path} ({len(image)} bytes)")
    print(f"Root filesystem: {len(root_payload)} bytes")
    print(f"Whole-image CRC: 0x{image_crc(image, new_global, erase_block_size):08x}")
    print(f"SHA-256: {hashlib.sha256(image).hexdigest()}")


if __name__ == "__main__":
    main()
