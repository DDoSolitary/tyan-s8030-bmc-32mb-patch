# Running BMC firmware v4.07+ on Tyan S8030 with 32 MiB SPI Flash

**DISCLAIMER: This document and the patch script are AI-generated and may contain false information. I manually reviewed most of the content and successfully upgraded my own S8030 board with 32 MiB flash. However, there is no guarantee that it will work on your hardware. Be prepared for the possibility of bricking the BMC (which will require recovery via U-Boot or a programmer) or even permanently damaging the hardware.**

## Background and scope

Early Tyan S8030 boards have a 32 MiB BMC SPI flash, while later boards have 64 MiB. Stock BMC v4.07 and the later versions covered here assume a 64 MiB device and cannot run unmodified on the earlier boards; v3.00 is the last firmware they can use without modification.

One hardware alternative is to replace the 32 MiB chip with a compatible 64 MiB SPI flash chip, as discussed on ServeTheHome. Section 6.5 covers that option; the firmware patch described here avoids soldering.

The normal payloads in the supported images occupy only about 30.17 MiB. The 64 MiB requirement comes from metadata placed at the end of a 64 MiB image and hard-coded flash-size assumptions in U-Boot and userspace, not from the AST2500 or the payload size. Relocating the metadata and correcting those assumptions therefore makes the firmware fit without removing functionality.

The conversion produces a 32 MiB raw image that is written to the BMC's SPI flash with ASPEED SoCFlash. The patcher supports **v4.07, v4.10, v4.11, v5.00, and v6.02**. Every supported version has passed a matched stock-versus-patched QEMU boot comparison; v4.07 and v6.02 have also been validated on a physical board.

On the physical board, IPMI, the web UI, sensors, remote KVM, and host boot/power control work with v4.07, though this does not guarantee compatibility with every board revision or hardware configuration.

**WARNING: v4.10, v4.11, and v5.00 have only been boot-tested in QEMU and have not been tested on a physical S8030 board. They may still fail on real hardware.**

**Only the versions explicitly listed as supported may be used with the patcher. Other stock firmware versions will not work unmodified on a 32 MiB board; each new image must be analyzed and added deliberately. Flashing an unmodified 64 MiB image will brick the BMC.**

Before flashing, make a raw backup of the BMC SPI chip; it contains both firmware and persistent configuration.

## Files and hashes

| File | Purpose | Size | SHA-256 |
|---|---|---:|---|
| `80300103.ima` | Stock BMC v3.00 | 33,554,432 bytes | `84b45653acf1d3e960d62a515e270be8e2181fa4b153fba5a15d9b19f21f161e` |
| `8030010407.ima` | Stock BMC v4.07 | 67,108,864 bytes | `edd24c9fa4437d0ab81f1f4a6f91410602097b455d40ce66d119a8a5d5c3bb6a` |
| `8030010407-32m.ima` | Patched v4.07 for 32 MiB | 33,554,432 bytes | `6e83eddc23db359111c26b5fddfe00bce9162cb21e0b4029bc353964194cf59a` |
| `8030010410.ima` | Stock BMC v4.10 | 67,108,864 bytes | `a4849b7d577e9d1c51a0f3e6dfa1b4e1637503182421df7a44f602db2a2e0018` |
| `8030010410-32m.ima` | Patched v4.10 for 32 MiB | 33,554,432 bytes | `838eade3aadcc35d7aeb1571ec1a404a7e9546a0f26f7b4c25522498886c4a03` |
| `8030010411.ima` | Stock BMC v4.11 | 67,108,864 bytes | `160ce96489bc0347279241d41f4bf84146d7f17c9e0516d06162fed4e57ca98b` |
| `8030010411-32m.ima` | Patched v4.11 for 32 MiB | 33,554,432 bytes | `c3110f3dda62f6641f35fa4d5497530df6a53ab68b74ded74c4fd2e26f15ce18` |
| `BMC8030010500.ima` | Stock BMC v5.00 | 67,108,864 bytes | `b3327ffb11b65e1382d0bace111adc8dcd95c2b33bdca45c8f674552d283bc41` |
| `BMC8030010500-32m.ima` | Patched v5.00 for 32 MiB | 33,554,432 bytes | `f157ab956d7dc35ceb6d082934332b36846d64e8c776821fff3613d3ade60be8` |
| `BMC8030010602.ima` | Stock BMC v6.02 | 67,108,864 bytes | `445e269d612f788e30a9194a625b2f2ed20cb4511d133dc859f720358c67a67a` |
| `BMC8030010602-32m.ima` | Patched v6.02 for 32 MiB | 33,554,432 bytes | `8387fdb5e891c7b47e7b826fbbd9991419d85f7b0840748864961f21dea2847f` |

The stock images come from Tyan's official S8030 BMC firmware update packages and are not included in this repository. Each patched image is generated locally from the corresponding stock image.

Literal metadata values are shown verbatim where relevant: v3.00 reports `3.00.1`, and v4.07 reports `4.07.0`.

Recommended recovery artifacts:

- The stock v3.00 image.
- A verified full-chip backup from the board before modification.
- The patched image for the selected supported version.
- A fresh full-chip backup of the working installation after configuration settles.

## 1. Firmware formats and stock layouts

### 1.1 Raw SPI images and FMH modules

The `.ima` files are raw SPI-flash images, not ordinary archives. They use AMI Flash Module Headers (FMH).

Most modules have a 64-byte `$MODULE$` header containing:

- Module name and version
- Module type
- Header and payload locations
- Allocated region size and actual payload size
- Flags and load address
- Payload CRC32
- An 8-bit header checksum and end signature

The boot module is special: its main header is near the end of its area, with an alternate FMH pointer at the end of the sector.

There are three relevant integrity mechanisms:

1. An 8-bit checksum over each 64-byte FMH header.
2. A CRC32 over each module payload.
3. A whole-image CRC32 stored in the global firmware-information module.

The whole-image CRC runs from the start of the image through the end of the global metadata erase block, excluding the header-checksum byte and the CRC field itself. These are integrity checksums, not cryptographic signatures that prevent modification.

### 1.2 Stock v3.00 layout

Important characteristics:

- U-Boot 2013.07 dated December 21, 2020.
- Linux 3.14.17-ami.
- CRAMFS root and web filesystems.
- Global firmware metadata at `0x01ff0000`.
- `FW_VERSION=3.00.1`
- `FW_CODEBASEVERSION=3.X`
- `FW_RELEASEID=RR9`
- `FW_DESC=RR11-5 AST2500 KERN3 UPDATE BUILD 1`
- U-Boot environment at `0x00040000`.

The meaningful modules include:

| Raw FMH name | Description | FMH offset | Payload offset and size |
|---|---|---:|---|
| `boot` | U-Boot | `0x0003ff00` | `0x0`, `0x0003acdc` bytes |
| `logo` | Logo | `0x00050000` | `0x00050040`, `0x00075738` bytes |
| `conf` | Configuration A | `0x00150000` | `0x00160000`, `0x001f0000` bytes |
| `conf` | Configuration B | `0x00350000` | `0x00360000`, `0x001f0000` bytes |
| `root` | Root CRAMFS | `0x00550000` | `0x00560000`, `0x010f0000` bytes |
| `osimage` | Kernel | `0x01650000` | `0x01650040`, `0x0029c7f8` bytes |
| `dre` | OEM Redfish/data-resource extensions | `0x018f0000` | `0x01900000`, `0x00070000` bytes |
| `www` | Web CRAMFS | `0x01970000` | `0x01980000`, `0x00470000` bytes |
| `ast2500e` | Global firmware info | `0x01ff0000` | `0x01ff0040`, `0x000000af` bytes |

On the running board, Linux creates six MTD partitions: the full flash, two configuration partitions, root, dre, and www.

### 1.3 Stock v4.07 layout

Important characteristics:

- U-Boot 2019.04 dated February 22, 2022.
- Linux 5.4.41-ami.
- SquashFS-XZ root and web filesystems.
- Global firmware metadata at `0x03ff0000`.
- `FW_VERSION=4.07.0`
- `FW_CODEBASEVERSION=5.X`
- `FW_RELEASEID=RR9`
- Description includes `AST2500EVB v12-update-3`.
- U-Boot environment at `0x00090000`.

| Raw FMH name | Description | FMH offset | Payload offset and size |
|---|---|---:|---|
| `boot` | U-Boot | `0x0008ff00` | `0x0`, `0x00085571` bytes |
| `conf` | Configuration A | `0x000a0000` | `0x000b0000`, `0x000f0000` bytes |
| `conf` | Configuration B | `0x001a0000` | `0x001b0000`, `0x000f0000` bytes |
| `dtb` | Kernel DTB | `0x002a0000` | `0x002a0040`, `0x000064f9` bytes |
| `root` | Root SquashFS | `0x002b0000` | `0x002c0000`, FMH size `0x011f9000` |
| `osimage` | Kernel uImage | `0x014c0000` | `0x014c0040`, `0x003406c0` bytes |
| `www` | Web SquashFS | `0x01810000` | `0x01820000`, `0x004eb000` bytes |
| `testapps` | Test applications | `0x01d10000` | `0x01d10040`, `0x000221ac` bytes |
| `bootlogo` | Boot logo | `0x01d40000` | `0x01d40040`, `0x000eaa70` bytes |
| `ast2500e` | Global firmware info | `0x03ff0000` | `0x03ff0040`, `0x000000b2` bytes |

The original root SquashFS has `0x011f8178` meaningful bytes and ends at `0x014b8178`; its FMH module ends at `0x014b9000` after padding. The web SquashFS has `0x004ea77f` meaningful bytes and ends at `0x01d0a77f`.

The last normal payload, the boot logo, ends at `0x01e2aab0`, approximately 30.17 MiB. Almost all of the upper 32 MiB in the 64 MiB file is erased `0xff`, except for the global metadata at the end.

## 2. Why stock v4.07 requires 64 MiB, and why 32 MiB is possible

The AST2500 does not inherently require 64 MiB. The important restrictions are firmware build assumptions and hard-coded constants.

### 2.1 Global metadata is outside a 32 MiB chip

The global FMH is at `0x03ff0000`, the final 64 KiB sector of a 64 MiB device. It cannot exist on a 32 MiB device. The firmware needs this metadata for version information and whole-image integrity.

### 2.2 U-Boot scans through the 64 MiB sector limit

The BootFMH scanner uses last-sector index `0x3ff`, which is 1024 sectors of 64 KiB, or 64 MiB.

On a 32 MiB device, out-of-range SPI reads wrap and expose the same modules a second time. A metadata-only conversion therefore enumerated duplicate modules and selected nonexistent `mtdblock7`, causing a root-mount failure. Limiting the scan to `0x1ff`, or 512 sectors, fixes it.

### 2.3 The helper reports a 64 MiB flash to userspace

`helper.ko` initializes `FlashSize` and `UsedFlashSize` to `0x04000000`. The flasher and other tools obtain these values through:

```text
/proc/sys/ractrends/Helper/FlashSize
/proc/sys/ractrends/Helper/UsedFlashSize
```

If left unchanged, the flasher can seek beyond the physical device.

### 2.4 The HPM update table uses a 64 MiB application size

The HPM table defines the application component as the total flash size minus the `0xa0000` boot/environment area. Stock values are `0x03f60000` and allocation `0x03f6000c`, based on 64 MiB.

### 2.5 Build-time configuration records also assume 64 MiB

`/etc/core_macros` and `/info/8030.PRJ` each contain two flash-size settings of `0x4000000`. These are changed for consistency with the runtime changes.

### 2.6 The meaningful payloads actually fit

All normal v4.07 payloads fit in approximately 30.17 MiB. Therefore, 32 MiB is sufficient if the metadata, scanner, and runtime size assumptions are corrected and all affected checksums are regenerated.

## 3. Exact changes in the patched v4.07 image

### 3.1 Global FMH relocation

The 64 KiB global metadata block is moved:

```text
0x03ff0000 -> 0x01ff0000
```

The FMH location and payload location are updated:

```text
FMH location:    0x03ff0000 -> 0x01ff0000
Module location: 0x03ff0040 -> 0x01ff0040
```

The firmware-information text remains intact, including `FW_VERSION=4.07.0` and `FW_CODEBASEVERSION=5.X`.

### 3.2 U-Boot scan bound

At raw image offset `0x57aec`, the 32-bit last-sector value changes:

```text
0x000003ff -> 0x000001ff
```

The U-Boot module CRC is regenerated. In v4.10, v4.11, v5.00, and v6.02, the table is at `0x57b6c`. The patcher detects it through its surrounding pointer relationships and requires a unique match rather than relying on either offset. It derives the erase-block size from the input image length and U-Boot sector count, then uses that size for metadata relocation and CRC coverage.

### 3.3 U-Boot recovery unlock length

The packet-based firmware recovery handler calls `flash_unlock(flash, 0, 0x4000000)`, requesting an unlock across the full stock chip. This is separate from normal boot. On a 32 MiB chip, the length must instead be `0x2000000` so the requested range does not exceed the device.

The immediate instruction changes from `e3a02301` (`mov r2, #0x4000000`) to `e3a02402` (`mov r2, #0x2000000`). It is at `0x5aac8` in v4.07 and `0x5ab4c` in the later supported versions. The patcher identifies the instruction and its surrounding unlock sequence rather than relying on those offsets, requires a unique match, and includes it in the U-Boot payload CRC update.

The recovery handler was exercised directly in QEMU with the corrected 32 MiB range and completed with the same status as before. Locked-region behavior has not been tested on physical hardware.

### 3.4 Helper instruction

In `/lib/modules/generic/misc/helper.ko`, at file offset `0x1988`, the ARM instruction changes:

```text
e3a02301  mov r2, #0x4000000
```

to:

```text
e3a02402  mov r2, #0x2000000
```

The patched helper writes 32 MiB to both `FlashSize` and `UsedFlashSize`. The patcher decodes ARM immediate MOV instructions, requires a unique match for the input flash size, and encodes the output size while preserving the destination register.

### 3.5 HPM application component

In `libipmihpm.so.6.22.11`, the APP entry at file offset `0xdcf8` changes:

```text
Application size:      0x03f60000 -> 0x01f60000
Allocation size:       0x03f6000c -> 0x01f6000c
```

These values represent 32 MiB minus the `0xa0000` boot/environment area. The patcher derives the reserved area and allocation adjustment from the input APP entry and recomputes both fields for the output flash size.

### 3.6 Configuration text

In both `/etc/core_macros` and `/info/8030.PRJ`, the two occurrences of:

```text
FLASH_SIZE=0x4000000
```

become:

```text
FLASH_SIZE=0x2000000
```

### 3.7 Checksums and filesystem packaging

The root filesystem is rebuilt with XZ compression and 128 KiB blocks. Its original file structure, symlinks, ownership, modes, and timestamps are preserved. The only intended rootfs content changes are:

- `etc/core_macros`
- `info/8030.PRJ`
- `lib/modules/generic/misc/helper.ko`
- `usr/local/lib/libipmihpm.so.6.22.11`

The following are regenerated as needed:

- Root module payload size and CRC
- U-Boot module CRC
- All affected FMH header checksums
- Global whole-image CRC and global header checksum

The resulting file is exactly 33,554,432 bytes. All normal module bounds and CRCs validate, as do the global FMH checksum and whole-image CRC.

### 3.8 Building the patched image

The patcher accepts only the exact stock image hashes listed in **Files and hashes** and automatically identifies the firmware version. It requires Python 3 and `unsquashfs`/`mksquashfs` from squashfs-tools, available on `PATH`.

Both input and output filenames are required:

```bash
python3 patch_8030_bmc_32m.py 8030010407.ima 8030010407-32m.ima
```

Use the corresponding explicit filenames for other supported versions.

The script:

- Refuses unsupported source images, unexpected module layouts, or occupied space above 32 MiB.
- Detects FMH locations and patch sites using structural and binary signatures, rejecting missing or ambiguous matches.
- Uses a temporary directory and removes it afterward.
- Preserves source files and does not modify the stock image.
- Applies the changes listed in this document and prints each modified offset with its old and new values, including regenerated CRCs and checksums.
- Validates output size, module bounds, FMH checksums, module CRCs, U-Boot scan bound, and whole-image CRC.

Do not use the patcher with an unlisted firmware version. Each new image must be analyzed even if its layout appears similar.

## 4. Verification and results

### 4.1 QEMU

For every supported version, stock and patched images were compared under matching AST2500 QEMU conditions, except for the emulated flash model.

Both the stock and patched images reached:

- `root=/dev/mtdblock3`
- Four FMH partitions
- Mounted SquashFS
- Init and runlevel 3
- IPMI, networking, lighttpd, Redis, event/task/Redfish services
- Flasher and component manager
- Boot Complete and login prompt

The stock images reported `0x4000000`; the patched images reported `0x2000000` for flash and used-flash sizes. No new boot or service failures were introduced.

#### QEMU invocations for the matched comparison

Run both instances for the same duration and capture their serial output. The following commands preserve the relevant QEMU arguments without depending on a particular local tool layout.

Unmodified v4.07 with a 64 MiB `w25q512jv` flash device:

```bash
qemu-system-arm \
  -M ast2500-evb,fmc-model=w25q512jv \
  -m 512M \
  -nographic \
  -monitor none \
  -drive file=8030010407.ima,format=raw,if=mtd,snapshot=on \
  -no-reboot
```

Patched v4.07 with a 32 MiB `mx25l25635e` flash device:

```bash
qemu-system-arm \
  -M ast2500-evb,fmc-model=mx25l25635e \
  -m 512M \
  -nographic \
  -monitor none \
  -drive file=8030010407-32m.ima,format=raw,if=mtd,snapshot=on \
  -no-reboot
```

Use a standard QEMU installation and compare the serial output. `snapshot=on` is important because it prevents changes to the original image files. `-no-reboot` makes QEMU exit if the guest resets.

The older v3.00 image stalled in QEMU at `Image to be booted is 1`. That was an emulation limitation, not a firmware defect; the same v3.00 image works on real hardware.

### 4.2 Physical S8030 hardware validation

#### v4.07

Patched v4.07 was ultimately written with SoCFlash. Results include:

- U-Boot 2019.04 detects 32 MiB.
- Linux 5.4.41 mounts root on `mtdblock3`.
- The second boot also succeeds, ruling out a one-time transient state.
- Both IPMItool and the web UI report 4.07.
- IPMI commands, sensors, remote KVM, and web UI work.
- The host OS boots.
- The flasher reports `FlashSize = UsedFlashSize = 0x2000000`.

There are no FMH errors, `mtdblock7` selections, root-mount failures, kernel panics, or rollback loops.

Not every peripheral or firmware-update path has been exhaustively tested. Treat future changes with the same caution as the initial conversion.

#### v6.02

Patched v6.02 was also flashed through the web UI on a 32 MiB board. The UART log confirms `DoFlash SUCCESS`, a successful reboot with root on `mtdblock3`, `FlashSize = UsedFlashSize = 0x2000000`, and `Boot Complete`. This validates both the web-update path and normal boot for v6.02 on physical hardware.

## 5. Flashing the patched image with SoCFlash

Use ASPEED SoCFlash on the S8030 host to back up the BMC SPI flash and write the selected patched image. The firmware update package includes Windows, Linux, and UEFI versions. The commands below use `8030010407-32m.ima`; substitute the corresponding patched filename for other supported versions.

This v3.00-to-v4.07 upgrade changes the firmware codebase. Tyan's release notes require SoCFlash for this transition; do not use the v3.00 web updater.

SoCFlash halts the BMC during both backup and flashing. The web UI, IPMI, KVM, and other BMC functions are unavailable during either operation; the host OS can remain running.

### 5.1 Back up and flash with one command

Run the following command on the S8030 host. This command syntax works with both older and newer SoCFlash versions:

```text
socflash 8030010407-32m.ima backup.bin
```

The first argument is the patched image to write; the second is the backup file. SoCFlash saves the existing chip contents to `backup.bin` before writing the new image. Do not substitute the original 64 MiB `8030010407.ima`.

After the command completes, verify that `backup.bin` is exactly `33554432` bytes and record its SHA-256 hash.

This full-image update replaces the configuration stored in the BMC SPI flash as well as the firmware, so settings return to the image defaults. Reapply the required settings after the update. The board's MAC address is stored in a separate I2C EEPROM, which SoCFlash does not overwrite. SoCFlash also leaves the host BIOS flash untouched.

The backup can restore the exact previous SPI state. A backup alone does not provide recovery if U-Boot is lost; a programmer or another low-level recovery path is still required.

### 5.2 If Linux SoCFlash reports MMIO errors

Some Linux host configurations prevent SoCFlash from accessing the AST2500 through MMIO, producing errors such as:

```text
MMIO Map Failed!
Can't Get VGA Information
segmentation fault
```

These errors concern host access, not the firmware image. Relevant checks include:

```bash
ls -l /dev/mem
sudo lspci -vv -s <BDF>
cat /sys/kernel/security/lockdown
cat /proc/cmdline
sudo dmesg | grep -iE 'devmem|iomem|lockdown|aspeed'
```

A temporary boot with `iomem=relaxed` commonly fixes `/dev/mem` restrictions. Remove that setting afterward.

If the `ast` driver owns the VGA device, temporarily unbind it from a non-VGA console:

```bash
echo <BDF> | sudo tee /sys/bus/pci/drivers/ast/unbind
```

Restore it afterward:

```bash
echo <BDF> | sudo tee /sys/bus/pci/drivers/ast/bind
```

### 5.3 Running SoCFlash from UEFI

The UEFI version does not depend on the host OS, avoiding the Linux host-access problems described above.

Use a FAT32-formatted drive or partition for the tool and images. Use the host's FAT32 EFI System Partition, another FAT32 partition, or a separate USB drive if necessary.

The built-in UEFI shell is available only with Secure Boot disabled. Disable Secure Boot for the maintenance operation and re-enable it afterward.

Use `map -r` to list filesystems, then select the FAT32 volume containing the tool and image. The following example assumes that it is `fs0`:

```text
map -r
fs0:
dir
```

After `dir` confirms that the files are present, run the same backup-and-flash command:

```text
socflash 8030010407-32m.ima backup.bin
```

### 5.4 Why the web updater is not used for this transition

The v3.00 web updater rejects this codebase change. An attempted update failed before U-Boot 2019.04 started:

```text
Cannot update different code base versions for ReleaseID RR9
Checksum verification failed for /mnt/fwupdate/rom.ima
```

The updater stopped at its codebase comparison before running the later integrity routine. The generic checksum message was the outer failure path. SoCFlash writes the patched image without passing through that old updater.

RR11.5 is Tyan/AMI's firmware release train, not a motherboard hardware revision. Stock v3.00 contains `RR11-5` in its description; v4.07 is the newer V12 codebase. After v4.xx, SoCFlash, Yafuflash, and the web interface are available, but stock 64 MiB images still must not be used on a 32 MiB chip.

## 6. Debugging and recovery

### 6.1 UART access on the S8030

#### 6.1.1 Connector identification

The relevant connector is labeled **BMC Debug**, next to the flash chip labeled **BMC**. A separate chip labeled BIOS is not the BMC flash.

The board uses a four-pin header with this order:

```text
VCC  TX  RX  GND
```

A triangle marks pin 1.

The chip model seen on this board, `MX25L25645GMI-08G`, is 256 Mbit = 32 MiB. The same capacity marking on a BIOS chip does not make it the BMC chip.

#### 6.1.2 Electrical connection

Use a 3.3 V TTL USB-UART adapter:

| S8030 header | Adapter |
|---|---|
| TX | RX |
| RX | TX |
| GND | GND |

Do not connect VCC. Only TX, RX, and GND are required for UART communication; an additional power connection is unnecessary and can cause backfeed or damage.

Use:

```text
115200 baud, 8 data bits, no parity, 1 stop bit, no flow control
```

For example:

```bash
tio -b 115200 /dev/ttyUSB0
```

If pin identification is uncertain:

1. With the board unpowered, use continuity mode to find GND relative to a known board ground.
2. Measure for a steady 3.3 V pin and mark it VCC; leave it disconnected.
3. Passively test the remaining signal pin through adapter RX until valid boot text appears.

Do not use resistance/continuity mode on a powered board, and do not short pins to identify them.

#### 6.1.3 U-Boot access and watchdog handling

Stock v3.00 has `bootdelay=0`. All later supported firmware versions have `bootdelay=2`.

To interrupt autoboot on any supported version, hold a printable key, such as the space bar, **before initiating the reset and continuously through the U-Boot banner**.

The BMC's hardware watchdogs can reset the system when they are not being serviced. Leaving U-Boot idle or running without the normal watchdog-management services can therefore lead to an automatic reset.

**WARNING: Disable the watchdogs before any U-Boot flashing operation. A watchdog reset during flash erase or programming may brick the BMC and require an external programmer for recovery.**

The AST2500 watchdog control register is at `base + 0x0c`. The following U-Boot commands disable watchdogs 1–3 for the current session:

```text
mw 0x1e78500c 0
mw 0x1e78502c 0
mw 0x1e78504c 0
```

These are hardware-register writes, not persistent firmware changes. Normal configuration is restored after reset. Use them only for controlled recovery work.

### 6.2 The `root` and `sysadmin` accounts

The account named `root` is UID 15000, while `sysadmin` is UID 0. Their privileges differ because of their UIDs.

Password changes for `sysadmin` can be made through the web UI at **Maintenance → System Administrator**.

#### 6.2.1 Getting a UID 0 shell on stock v3.00

For recovery work that requires UID 0 on stock v3.00, boot the existing kernel with `init=/bin/sh`. This bypasses login and starts PID 1 as UID 0 without modifying the firmware.

This U-Boot has no `sf` command. Copy the stock kernel from the flash mapping with `cp.b`:

```text
mw 0x1e78500c 0
mw 0x1e78502c 0
mw 0x1e78504c 0
cp.b 0x21650040 0x80100000 0x29c7f8
setenv bootargs root=/dev/mtdblock3 ro ip=none mem=488M console=ttyS2,115200 rootfstype=cramfs bigphysarea=8192 init=/bin/sh
bootm 0x80100000
```

The first four bytes at `0x21650040` should be `27 05 19 56`, the uImage magic. `cp.b` copies into RAM only.

At the resulting root prompt:

```sh
id
mount -t proc proc /proc
cat /proc/mtd
```

Normal startup scripts do not run with this method. Use it only for controlled recovery work.

### 6.3 Optional physical 64 MiB chip replacement

This replacement was not physically tested during the firmware-patching work. The component and programmer suggestions below are based on specifications, not a verified S8030 hardware conversion.

If replacing rather than patching, use a 512 Mbit, 3.3 V SPI NOR chip in the same SOP16/SOIC-16 300-mil package.

Suitable parts include:

- Macronix `MX25L51245GMI-08G`: 64 MiB, 2.7–3.6 V, SOIC-16.
- Winbond `W25Q512JVFIQ`: 64 MiB, 2.7–3.6 V, SOIC-16 300 mil.

Avoid `W25Q512JVEIQ`, which is a different WSON package, and avoid 1.8 V or BGA variants.

A practical programmer setup is:

- XGecu T48 (TL866-3G)
- SOP16-to-DIP16 300-mil adapter for a loose chip
- Optional SOP16 test clip for reading the installed chip

Verify that the programmer software lists the exact chip and verify the written data by readback. A CH341A is cheaper but more prone to voltage, support, and in-circuit reliability issues.

Program a new 64 MiB chip with the original unmodified `8030010407.ima`, not the 32 MiB patch. Preserve the old chip and record the BMC MAC address and configuration before replacement. A repair technician can perform the soldering if needed.

### 6.4 Future upgrades and recovery checklist

**Flashing any unmodified 64 MiB stock image will brick the BMC on a 32 MiB board.** The v3.00 web updater rejected v4.07 because of its cross-codebase check; that check does not protect updates within the v4.xx codebase. Flash only the patched output from a supported stock image. Any newly released image must be analyzed and explicitly added to the patcher before use.

Before any update:

- Confirm the exact board and flash-chip model.
- Make a full-chip backup and verify its size and hash.
- Keep the stock v3.00 image and current working backup available.
- Check module payloads, global FMH location, U-Boot scan limits, helper values, and HPM sizes.
- Regenerate every affected module CRC and the whole-image CRC.
- Boot-test in QEMU where useful, but require real hardware confirmation.
- Use SoCFlash for the RR11.5-to-V12 transition.
- Keep UART recording active through flashing and the first several boots.
- Verify the reported firmware version, root device, flash sizes, IPMI, networking, sensors, KVM, and host power control.
- Make a fresh backup of the working installation.

### 6.5 Common non-fatal log messages

The following messages were captured during a normal boot of the patched v4.07 installation. The BMC reached `Boot Complete`, and IPMI, sensors, the web UI, and remote KVM worked:

```text
[    2.212546] mtd: partition "root" doesn't end on an erase/write block -- force read-only
[    2.228288] mtd: partition "www" doesn't end on an erase/write block -- force read-only
[    2.257996] ftgmac100 1e680000.ethernet: Error applying setting, reverse things back
[    2.480578] hctosys: unable to open rtc device (rtc0)
mkdir: can't create directory '/conf/crontabs': Read-only file system
[   12.859897] platform: loading out-of-tree module taints kernel.
[!] sys_time_sync - error on sync system time
[522 : 522 CRITICAL][nwcfg.c:5980]IOCTL to get ETHCFG failed
[1260 : 1260 CRITICAL][libipmi_AMIOEM.c:4179]Error in Get Service Configuration in raw IPMI command layer::403
2000-01-01 00:00:46: (configfile.c.1466) server.upload-dirs doesn't exist: /usr/local/lmedia/lost+found
RMEDIA is not enabled
[   94.335073] NCSI(eth1): Setting User Config Failed
Starting system message bus: dbusdbus[1653]: Unknown group "netdev" in message bus configuration file
sh: true: unknown operand
```

These messages concern expected read-only filesystems, an unset clock, optional interfaces or disabled services, and non-fatal OEM configuration and script warnings—not flash layout or image integrity. The boot continues through root mounting and service initialization to `Boot Complete`, and the tested management functions work. A failed conversion instead involves problems such as incorrect FMH discovery or root MTD selection, root-mount failure, or a reboot loop.
