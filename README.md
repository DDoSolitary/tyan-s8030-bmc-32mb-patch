# Running BMC v4.07 on a Tyan S8030 with 32 MiB SPI Flash

**DISCLAIMER: This document and the patch script are AI-generated and may contain false information. I manually reviewed most of the content and successfully upgraded my own S8030 board with 32 MiB flash. However, there is no guarantee that it will work on your hardware. Be prepared for the possibility of bricking the BMC (which will require recovery via U-Boot or a programmer) or even permanently damaging the hardware.**

## Background and scope

Early Tyan S8030 boards have a 32 MiB BMC SPI flash, while later boards have 64 MiB. Stock BMC v4.07 assumes a 64 MiB device and cannot run unmodified on the earlier boards; v3.00 is the last firmware they can use without modification.

One hardware alternative is to replace the 32 MiB chip with a compatible 64 MiB SPI flash chip, as discussed on ServeTheHome. Section 6.5 covers that option; the firmware patch described here avoids soldering.

The normal v4.07 payloads occupy only about 30.17 MiB. The 64 MiB requirement comes from metadata placed at the end of a 64 MiB image and hard-coded flash-size assumptions in U-Boot and userspace, not from the AST2500 or the payload size. Relocating the metadata and correcting those assumptions therefore makes v4.07 fit without removing functionality.

The conversion produces `8030010407-32m.ima`, a 32 MiB raw image that is written to the BMC's SPI flash with ASPEED SoCFlash. This document records the analysis, exact changes, and validation of that conversion.

The patched image boots successfully on a real S8030 board. IPMI, the web UI, sensors, remote KVM, and host boot/power control work, though this does not guarantee compatibility with every board revision or hardware configuration.

**This patch is specific to v4.07. Future stock firmware versions will not work unmodified on a 32 MiB board; every later image must be analyzed and patched again. Flashing an unmodified 64 MiB image will brick the BMC.**

Before flashing, make a raw backup of the BMC SPI chip; it contains both firmware and persistent configuration.

## Files and hashes

| File | Purpose | Size | SHA-256 |
|---|---|---:|---|
| `80300103.ima` | Stock BMC v3.00 | 33,554,432 bytes | `84b45653acf1d3e960d62a515e270be8e2181fa4b153fba5a15d9b19f21f161e` |
| `8030010407.ima` | Stock BMC v4.07 | 67,108,864 bytes | `edd24c9fa4437d0ab81f1f4a6f91410602097b455d40ce66d119a8a5d5c3bb6a` |
| `8030010407-32m.ima` | Patched v4.07 for 32 MiB | 33,554,432 bytes | `1080d54562f6ac93457c4f4f3a99931477112bb588d94677f2110a9a8c6f1ef0` |

The stock images come from Tyan's official S8030 BMC firmware update packages and are not included in this repository. The patched image is generated locally by running the patcher on the stock v4.07 image.

The release names used throughout this document are **v3.00** and **v4.07**. Literal metadata values are shown verbatim where relevant: v3.00 reports `3.00.1`, and v4.07 reports `4.07.0`.

Recommended recovery artifacts:

- The stock v3.00 image.
- A verified full-chip backup from the board before modification.
- The patched v4.07 image.
- A fresh full-chip backup of the working v4.07 installation after configuration settles.

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

The U-Boot module CRC is regenerated.

### 3.3 Helper instruction

In `/lib/modules/generic/misc/helper.ko`, at file offset `0x1988`, the ARM instruction changes:

```text
e3a02301  mov r2, #0x4000000
```

to:

```text
e3a02402  mov r2, #0x2000000
```

The patched helper writes 32 MiB to both `FlashSize` and `UsedFlashSize`.

### 3.4 HPM application component

In `libipmihpm.so.6.22.11`, the APP entry at file offset `0xdcf8` changes:

```text
Application size:      0x03f60000 -> 0x01f60000
Allocation size:       0x03f6000c -> 0x01f6000c
```

These values represent 32 MiB minus the `0xa0000` boot/environment area.

### 3.5 Configuration text

In both `/etc/core_macros` and `/info/8030.PRJ`, the two occurrences of:

```text
FLASH_SIZE=0x4000000
```

become:

```text
FLASH_SIZE=0x2000000
```

### 3.6 Checksums and filesystem packaging

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

### 3.7 Building the patched image

The patcher is pinned to the exact supplied v4.07 source hash. It requires Python 3 and `unsquashfs`/`mksquashfs` from squashfs-tools.

```bash
python3 patch_8030_bmc_32m.py 8030010407.ima 8030010407-32m.ima
```

Optional arguments select the tool paths:

```bash
python3 patch_8030_bmc_32m.py input.ima output.ima \
  --unsquashfs /path/to/unsquashfs \
  --mksquashfs /path/to/mksquashfs
```

The script:

- Refuses unexpected source images or occupied space above 32 MiB.
- Uses a temporary directory under the current working directory and removes it afterward.
- Preserves source files and does not modify the stock image.
- Applies the changes listed in this document.
- Validates output size, module bounds, FMH checksums, module CRCs, U-Boot scan bound, and whole-image CRC.

Do not generalize the hard-coded offsets to another firmware version without revalidating them.

## 4. Verification and results

### 4.1 QEMU

Stock and patched v4.07 were compared under matching AST2500 QEMU conditions, except for the emulated flash model.

Both reached:

- `root=/dev/mtdblock3`
- Four FMH partitions
- Mounted SquashFS
- Init and runlevel 3
- IPMI, networking, lighttpd, Redis, event/task/Redfish services
- Flasher and component manager
- Boot Complete and login prompt

The stock image reported `0x4000000`; the patched image reported `0x2000000` for flash and used-flash sizes. No new boot or service failures were introduced.

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

### 4.2 Real S8030 hardware

The patched image was ultimately written with SoCFlash. Results include:

- U-Boot 2019.04 detects 32 MiB.
- Linux 5.4.41 mounts root on `mtdblock3`.
- The second boot also succeeds, ruling out a one-time transient state.
- Both IPMItool and the web UI report 4.07.
- IPMI commands, sensors, remote KVM, and web UI work.
- The host OS boots.
- The flasher reports `FlashSize = UsedFlashSize = 0x2000000`.

There are no FMH errors, `mtdblock7` selections, root-mount failures, kernel panics, or rollback loops.

Not every peripheral or firmware-update path has been exhaustively tested. Treat future changes with the same caution as the initial conversion.

## 5. Flashing the patched image with SoCFlash

Use ASPEED SoCFlash on the S8030 host to back up the BMC SPI flash and write `8030010407-32m.ima`. The firmware update package includes Windows, Linux, and UEFI versions.

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

#### 6.1.3 Getting a U-Boot prompt

The stock bootloader has `bootdelay=0`. The `Hit any key to stop autoboot: 0` message does not provide a practical countdown.

Hold a printable key, such as the space bar, **before initiating the reset and continuously through the U-Boot banner**. Merely pressing keys quickly after seeing the message is usually too late.

### 6.2 Watchdog resets during manual booting

Leaving U-Boot idle or running a shell through `init=/bin/sh` can lead to an automatic reset while the normal watchdog-management services are not running.

The AST2500 watchdog control register is at `base + 0x0c`. The following U-Boot commands disable watchdogs 1–3 for the current session:

```text
mw 0x1e78500c 0
mw 0x1e78502c 0
mw 0x1e78504c 0
```

These are hardware-register writes, not persistent firmware changes. Normal configuration is restored after reset. Use them only for controlled recovery work.

### 6.3 Getting UID 0 without modifying flash

The existing v3.00 kernel can be started directly with `init=/bin/sh`, which bypasses login and starts PID 1 as UID 0.

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

Normal startup scripts do not run with this method.

### 6.4 The `sysadmin` account and password handling

This procedure sets a usable password for the existing UID 0 `sysadmin` account so it can be used during a normal boot. The account named `root` is UID 15000.

```text
sysadmin:x:0:0:sysadmin:/root:/usr/local/bin/defshell
root:x:15000:15000:root:/root:/usr/local/bin/defshell
```

Perform the edits from the **temporary UID-0 shell** described in section 6.3.

#### 6.4.1 Mount the configuration files

In the temporary root shell, mount `/proc` and the writable JFFS2 configuration partition, then back up the account files:

```sh
mount -t proc proc /proc
mount -t jffs2 /dev/mtdblock1 /conf
cp /conf/passwd /conf/passwd.bak
cp /conf/shadow /conf/shadow.bak
```

`/etc/passwd` and `/etc/shadow` are symlinks to `/conf/passwd` and `/conf/shadow`.

#### 6.4.2 Edit the password with `vi`

Open the shadow file with:

```sh
vi /conf/shadow
```

Use one of these password hashes for `sysadmin`:

- The working SHA-256 hash from the existing `root` entry, which makes the `sysadmin` password identical to the web-`root` password, or
- A new SHA-256 hash generated with `openssl passwd -5`.

Set the last-change field to `14386` and leave the remaining aging fields in their original form:

```text
sysadmin:$5$<SHA-256-hash>:14386:0:99999:7:::
```

Avoid running BusyBox `passwd` for this change: it can create a legacy DES hash and a zero last-change timestamp. The BMC also starts at Unix epoch, 1970-01-01, until network time is synchronized, which can trigger forced-password-change behavior.

Save the files, then apply the changes and reboot into the normal firmware:

```sh
sync
umount /conf
reboot -f
```

After the normal boot, log in as `sysadmin` with the password corresponding to the installed hash. If the hash was copied from `root`, use the existing web-`root` password; otherwise, use the password chosen when running `openssl passwd -5`. The `root` web account remains unchanged.

### 6.5 Optional physical 64 MiB chip replacement

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

### 6.6 Future upgrades and recovery checklist

**Flashing a later unmodified 64 MiB stock image will brick the BMC on a 32 MiB board.** The v3.00 web updater rejected v4.07 because of its cross-codebase check; that check does not protect updates within the v4.xx codebase. Every later stock image must be reviewed and converted again before flashing.

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

### 6.7 Common non-fatal log messages

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
