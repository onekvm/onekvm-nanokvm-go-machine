# onekvm-nanokvm-go-machine

English | [简体中文](README.zh-CN.md)

Machine files for the Sipeed NanoKVM Go (AX620Q, ARM64), built by `onekvm-distro`
(`meta-onekvm-nanokvm-go`).

Status: bring-up. The AArch64 SPL/BL31/U-Boot has not passed hardware validation
yet (`hardware_qualified: false` in the firmware manifest).

## Packages

| Package | Files | Content |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init`, `boot-splash/` | Initramfs `/init`: arms the hardware watchdog, shows the boot logo, then mounts the SD root slot below the userdata overlay, or a Wi-Fi NFS root |
| `onekvm-nanokvm-go-boot` | `watchdog.c`, `watchdog-handoff`, `go-udhcpc` | Boot watchdog feeder and its hand-off; address, routes and DNS for the NFS root |
| `onekvm-machine-nanokvm-go` | `machine.json`, `edid/`, `*.service`, `*.conf`, `99-peripherals.rules`, `grow-root` | Machine identity, EDID presets, systemd units and drop-ins, udev rules, root expansion for single-root SD cards |

## Build tools

Used by the `onekvm-distro` firmware, boot image and SD image recipes. None of them writes to a device.

| Tool | Output |
| --- | --- |
| `package-firmware.py` | ROM `boot.bin` (signed SPL, two copies), `atf.img` (BL31) and `uboot.bin` |
| `package-boot.py` | Axera `kernel.img` (AArch64 Image with initramfs) and `dtb.img`; rejects images that would overlap BL31 or the DTB |
| `build-sd.py` | A/B SD image; checks the firmware and boot files against their manifests and the root image against the slot size |

## SD card layout

Same slot sizes as NanoKVM Pro SD, 512-byte sectors:

| Partition | Start sector | Content |
| --- | ---: | --- |
| MBR | 0 | Partition table only. BootROM and SPL load `boot.bin` and the files after it from the first FAT partition |
| p1, FAT32, 128 MiB, active, label `ONEKVMBOOT` | 2048 | `boot.bin`, `uboot.bin`, `atf.img`, `kernel-a.img`/`dtb-a.img`, `kernel-b.img`/`dtb-b.img`, `boot-slot`, and the `onekvm/` settings folder |
| p2, 128 MiB | 264192 | Read-only root A, SquashFS |
| p3, 128 MiB | 526336 | Read-only root B, SquashFS; the image writes the same root to both slots |
| p4, userdata | 788480 | Created on the first SD boot up to the end of the card and formatted f2fs. Existing f2fs or ext4 userdata is kept |

`boot-slot` holds `A` or `B`. U-Boot loads that slot's kernel and DTB, or the other
slot's when they are missing or invalid, and passes `rauc.slot=` to Linux. The
initramfs mounts the slot's root read-only below an overlay whose `upper` and `work`
directories on userdata both slots share; `/mnt/storage` is the userdata `storage`
directory. If userdata cannot be mounted, Go still boots, and changes last until the
next reboot. A card without `boot-slot` keeps the earlier layout: `kernel.img` and
`dtb.img` on FAT, and an ext4 root in p2 that grows to the end of the card.

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

## Watchdog

Any boot failure in the initramfs waits for a hardware watchdog reset instead of
stopping. The boot feeder gives Linux up to 240 seconds to reach the runtime
watchdog, which takes over before `onekvm.service` starts. OneKVM reboots also
go through a 5 second hardware watchdog reset.

## EDID

| Preset | Mode |
| --- | --- |
| `factory.edid.bin` | Default, identical to `1080p60.edid.bin` |
| `1080p60.edid.bin` | 1920×1080 @ 60 Hz |
| `720p60.edid.bin` | 1280×720 @ 60 Hz |

## Tests

`make test` checks the boot inputs (`package-boot.py`) and the boot splash rendering.

## License

GPL-2.0-only, see `LICENSE`. Files with an `SPDX-License-Identifier: MIT` header,
such as `watchdog.c` and `boot-splash/`, are MIT, see `LICENSES/MIT.txt`.
