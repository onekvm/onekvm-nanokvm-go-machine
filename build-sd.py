#!/usr/bin/env python3
"""Create a Go A/B SD image from checked boot inputs; never write devices.

512-byte sectors, the slot geometry of the NanoKVM Pro SD:

  0       MBR. BootROM and SPL only read its partition table: FatFs takes the
          first FAT partition and loads 0:boot.bin. The boot code area stays zero.
  2048    p1 FAT32 128 MiB, active: SPL, BL31, U-Boot, slot A boot image, boot-slot
  264192  p2 read-only root A, 128 MiB SquashFS
  526336  p3 read-only root B, 128 MiB
  788480  p4 userdata, created by the initramfs on first boot up to the card end

The image holds only the MBR and the used part of the FAT: the root image is
the FAT's last file, onekvm/root.squashfs. The first boot from slot A writes
it to p2 and p3 and copies the slot A boot image into slot B, as asked by
onekvm/install-b. Writing the image leaves the rest of the card untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

DISK_ID = 0x4F4B5647  # OneKVM Go A/B layout; NanoKVM Pro SD uses 0x4F4B5650
BOOT_START = 2048
BOOT_SECTORS = 262144
SLOT_SECTORS = 262144
ROOT_A = BOOT_START + BOOT_SECTORS
ROOT_B = ROOT_A + SLOT_SECTORS
USERDATA_START = ROOT_B + SLOT_SECTORS
SQUASHFS_MAGIC = 0x73717368  # "hsqs" at offset 0
FIRMWARE = ("boot.bin", "atf.img", "uboot.bin")
ROOT_FILE = "onekvm/root.squashfs"
INSTALL_MARKER = "onekvm/install-b"


def slot_files(boot_manifest):
    """Boot image -> per-slot FAT name pattern, by the boot manifest's format.

    A mainline U-Boot boots one FIT per slot; the vendor U-Boot boots an Axera
    kernel and DTB capsule pair per slot.
    """
    if boot_manifest.get("format", "capsule") == "fit":
        return {"boot.itb": "boot-%s.itb"}
    return {"kernel.img": "kernel-%s.img", "dtb.img": "dtb-%s.img"}


def check_inputs(firmware, boot):
    manifest = json.loads((firmware / "manifest.json").read_text())
    images = json.loads((boot / "manifest.json").read_text())
    if manifest["architecture"] != "arm64" or images["architecture"] != "arm64":
        raise ValueError("ARM64 firmware and boot images must be paired")
    for directory, name, digest in (
            *((firmware, name, manifest[name]["sha256"]) for name in FIRMWARE),
            *((boot, name, images[name]["sha256"]) for name in slot_files(images))):
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("boot input identity differs: " + name)
    return images


def check_root(rootfs):
    size = rootfs.stat().st_size
    with rootfs.open("rb") as f:
        magic = struct.unpack("<I", f.read(4))[0]
    if magic != SQUASHFS_MAGIC:
        raise ValueError("root image is not SquashFS")
    if not 0 < size <= SLOT_SECTORS * 512:
        raise ValueError("root image is %d bytes; a slot holds %d" % (size, SLOT_SECTORS * 512))
    # The initramfs reads the slots back in 4 KiB blocks.
    if size % 4096:
        raise ValueError("root image is not padded to 4 KiB")


def mbr():
    table = bytearray(512)
    struct.pack_into("<I", table, 440, DISK_ID)
    # CHS fields are saturated; firmware and Linux use LBA.
    for n, (active, typ, start, size) in enumerate((
            (0x80, 0x0C, BOOT_START, BOOT_SECTORS),
            (0, 0x83, ROOT_A, SLOT_SECTORS),
            (0, 0x83, ROOT_B, SLOT_SECTORS))):
        struct.pack_into("<B3sB3sII", table, 446 + n * 16, active, b"\xfe\xff\xff", typ,
                         b"\xfe\xff\xff", start, size)
    table[510:512] = b"\x55\xaa"
    return bytes(table)


def boot_fat(path, firmware, boot, images, rootfs):
    with path.open("wb") as f:
        f.truncate(BOOT_SECTORS * 512)
    # Hidden sectors = partition start, as on factory cards read by BootROM.
    subprocess.run(["mkfs.vfat", "--invariant", "-F", "32", "-h", str(BOOT_START), "-n", "ONEKVMBOOT",
                    str(path)], check=True)
    files = [(firmware / name, name) for name in FIRMWARE]
    files += [(boot / name, pattern % "a") for name, pattern in slot_files(images).items()]
    slot_file = path.with_suffix(".slot")
    slot_file.write_text("A\n")
    files.append((slot_file, "boot-slot"))
    marker = path.with_suffix(".install")
    marker.write_text("The first boot from slot A copies the slot A boot images into slot B.\n")
    for source, name in files:
        subprocess.run(["mcopy", "-i", str(path), str(source), "::" + name], check=True)
    subprocess.run(["mmd", "-i", str(path), "::onekvm"], check=True)
    subprocess.run(["mcopy", "-i", str(path), str(marker), "::" + INSTALL_MARKER], check=True)
    slot_file.unlink()
    marker.unlink()
    # Last, so that the image ends with it.
    if subprocess.run(["mcopy", "-i", str(path), str(rootfs), "::" + ROOT_FILE]).returncode:
        raise ValueError("the root image does not fit on the boot FAT next to the boot images")


def fat_end(path):
    """Length of the FAT32 image up to the end of its last allocated cluster."""
    with path.open("rb") as f:
        boot = f.read(512)
        sector, cluster, reserved, fats = struct.unpack_from("<HBHB", boot, 11)
        fat_sectors = struct.unpack_from("<I", boot, 36)[0]
        f.seek(reserved * sector)
        table = f.read(fat_sectors * sector)
    entries = struct.unpack("<%dI" % (len(table) // 4), table)
    last = max(n for n in range(2, len(entries)) if entries[n] & 0x0FFFFFFF)
    # Cluster 2 is the first cluster of the data area.
    return (reserved + fats * fat_sectors + (last - 1) * cluster) * sector


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("firmware", "boot", "rootfs", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    images = check_inputs(a.firmware, a.boot)
    check_root(a.rootfs)
    bootfs = a.output.with_suffix(".fat")
    boot_fat(bootfs, a.firmware, a.boot, images, a.rootfs)
    end = fat_end(bootfs)
    with a.output.open("wb") as f:
        f.truncate(BOOT_START * 512 + end)
        f.write(mbr())
    # Free FAT space after the last cluster stays as it is on the card.
    subprocess.run(["dd", "if=" + str(bootfs), "of=" + str(a.output), "bs=512", "seek=" + str(BOOT_START),
                    "count=" + str(end), "iflag=count_bytes", "conv=notrunc,sparse", "status=none"],
                   check=True)
    bootfs.unlink()


if __name__ == "__main__":
    main()
