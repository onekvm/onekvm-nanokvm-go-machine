# onekvm-nanokvm-go-machine

English | [简体中文](README.zh-CN.md)

Machine files for the Sipeed NanoKVM Go (AX620Q, ARM64), built by `onekvm-distro`
(`meta-sipeed/meta-nanokvm-go`).

Status: bring-up. The AArch64 SPL/BL31/U-Boot cold-boots one Go from an SD card
with the factory-formatted FAT; the firmware manifest still says
`hardware_qualified: false`.

## Packages

| Package | Files | Content |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init` | Initramfs `/init`: arms the hardware watchdog, shows the boot logo and keeps its boot bar moving, runs the shared Recovery checks, then mounts the SD root slot below the userdata overlay, or a Wi-Fi NFS root |
| `onekvm-boot-splash` | `boot-splash/` | Boot logo, boot bar and its animator on the panel, for the initramfs, the root system and the screen service |
| `onekvm-boot-progress` | `boot-splash/systemd/` | Units that advance the boot bar while the root system starts |
| `onekvm-nanokvm-go-boot` | `watchdog.c`, `watchdog-handoff`, `go-udhcpc` | Boot watchdog feeder and its hand-off; address, routes and DNS for the NFS root |
| `onekvm-machine-nanokvm-go` | `machine.json`, `edid/`, `*.service`, `*.conf`, `99-peripherals.rules`, `grow-root` | Machine identity, EDID presets, systemd units and drop-ins, udev rules, root expansion for single-root SD cards |

## Build tools

Used by the `onekvm-distro` firmware, boot image and SD image recipes. None of them writes to a device.

| Tool | Output |
| --- | --- |
| `package-firmware.py` | ROM `boot.bin` (signed SPL, two copies), `atf.img` (BL31) and `uboot.bin` |
| `package-fit.py` | `boot.itb` for the mainline U-Boot: one FIT with the AArch64 Image (with initramfs, gzip) and the Go DTB, each with a SHA-256 hash, and the image's build ID in its configuration; rejects the same images as `package-boot.py`, a DTB that is not for the Go, and a FIT larger than the U-Boot load window |
| `package-boot.py` | Axera `kernel.img` (AArch64 Image with initramfs) and `dtb.img` for the vendor U-Boot; rejects images that would overlap BL31 or the DTB |
| `build-sd.py` | A/B SD image from either boot format; checks the firmware and boot files against their manifests and the root image against the slot size and the free FAT space |

## SD card layout

Same slot sizes as NanoKVM Pro SD, 512-byte sectors:

| Partition | Start sector | Content |
| --- | ---: | --- |
| MBR | 0 | Partition table only. BootROM and SPL load `boot.bin` and the files after it from the first FAT partition |
| p1, FAT32, 128 MiB, active, label `ONEKVMBOOT` | 2048 | `boot.bin`, `uboot.bin`, `atf.img`, the boot images of both slots, `boot-slot`, and the `onekvm/` settings folder. The SD image carries slot A's boot images and the root image to install |
| p2, 128 MiB | 264192 | Read-only root A, SquashFS. The SD image does not cover it; see below |
| p3, 128 MiB | 526336 | Read-only root B, SquashFS. The SD image does not cover it; see below |
| p4, userdata | 788480 | Created on the first SD boot up to the end of the card and formatted f2fs. Existing f2fs or ext4 userdata is kept |

Each slot's boot images depend on the U-Boot:

| U-Boot | Slot A | Slot B |
| --- | --- | --- |
| Mainline | `boot-a.itb` | `boot-b.itb` |
| Vendor | `kernel-a.img`, `dtb-a.img` | `kernel-b.img`, `dtb-b.img` |

`boot-slot` holds `A` or `B`. U-Boot loads that slot's boot images, or the other
slot's when they are missing or invalid, and passes `rauc.slot=` and the FIT's
`onekvm.build-id=` to Linux. A slot installed by an update boots on trial:
`boot-trial/tries` holds its attempts left, and U-Boot boots the other slot once
none are left. Linux deletes the file when the slot passes its health check. The
initramfs mounts the slot's root read-only below an overlay whose `upper` and `work`
directories on userdata both slots share; `/mnt/storage` is the userdata `storage`
directory. Before mounting it, the initramfs removes upper copies of files that
the new image ships, as on NanoKVM Pro, so they cannot hide the image's versions.
If userdata cannot be mounted, Go still boots, and changes last until the
next reboot.

The SD image holds only the partition table and the used part of the FAT, so
writing it leaves both root slots and userdata untouched. The root image is
the FAT file `onekvm/root.squashfs`. The first boot from slot A shows the
installing screen, writes that file to p2 and p3, reads both back from the card
and deletes the file; slot A does not boot until this succeeds. It then copies
slot A's boot images to slot B as `onekvm/install-b` asks, reads them back and
deletes that file; if this fails, slot A boots without a fallback slot. An
interrupted install runs again at the next boot. An update written to slot B
deletes `onekvm/install-b` too. The root image must fit on the FAT next to the
boot images, about 110 MiB.

A card without `boot-slot` keeps the earlier layout: `boot.itb` (mainline
U-Boot) or `kernel.img` and `dtb.img` (vendor U-Boot) on FAT, and an ext4 root in p2 that
grows to the end of the card.

## Wi-Fi NFS root

Without `onekvm/nfs-root` on the FAT partition, Go boots from the SD root. To boot
from NFS over Wi-Fi, add two files to the `onekvm/` folder:

| File | Content |
| --- | --- |
| `nfs-root` | One line: `<server-ip>:/absolute/export`, IPv4 only, e.g. `192.0.2.10:/srv/nfs/onekvm/nanokvm-go` |
| `wpa_supplicant.conf` | A wpa_supplicant configuration for the Wi-Fi network |

The root is mounted with NFSv3 over TCP (`hard,nolock`); the export must allow root
writes. The initramfs keeps the Wi-Fi connection for the whole session, so do not
change that Wi-Fi connection while the system is running. The NFS root is never resized.

## Recovery

Before mounting any SD partition, the initramfs runs the Recovery hook from
`onekvm-nanokvm-recovery`. It enters Recovery when BOOT is held at power-on,
after Recovery in the system settings, with `onekvm_recovery=1`, or after ten
boots that did not run normally for two minutes; it also performs a requested
factory reset. A new card gets its userdata partition first, because Recovery
checks all four partitions. NFS boots never clear the counter, so they do not
count. Recovery exports the whole SD card as USB mass storage with an ACM
console, also on UART3, and the screen says to write the image to it. In
the console, `reboot` restarts. If the checks fail, normal boot continues.

## Watchdog

Any boot failure in the initramfs waits for a hardware watchdog reset instead of
stopping. The boot feeder gives Linux up to 240 seconds to reach the runtime
watchdog, which takes over before `onekvm.service` starts. In Recovery it keeps
feeding without that limit. OneKVM reboots also go through a 5 second hardware
watchdog reset.

## EDID

| Preset | Mode |
| --- | --- |
| `factory.edid.bin` | Default 1920×1080 @ 60 Hz, the same file on every machine (`onekvm-machine-files.inc` in `onekvm-distro`) |
| `1080p60.edid.bin` | 1920×1080 @ 60 Hz, from `onekvm-distro` |
| `720p60.edid.bin` | 1280×720 @ 60 Hz |

## Tests

`make test` checks the boot inputs (`package-fit.py`, `package-boot.py`), the SD layout, the boot splash rendering, the boot bar and its hand-off from the initramfs to the root system.

## License

GPL-2.0-only, see `LICENSE`. Files with an `SPDX-License-Identifier: MIT` header,
such as `watchdog.c` and `boot-splash/`, are MIT, see `LICENSES/MIT.txt`.
