# onekvm-nanokvm-go-machine

English | [简体中文](README.zh-CN.md)

Machine files for the Sipeed NanoKVM Go (AX620Q, ARM64), built by `onekvm-distro`
(`meta-onekvm-nanokvm-go`).

Status: bring-up. The AArch64 SPL/BL31/U-Boot has not passed hardware validation
yet (`hardware_qualified: false` in the firmware manifest).

## Packages

| Package | Files | Content |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init`, `boot-splash/` | Initramfs `/init`: arms the hardware watchdog, shows the boot logo, then mounts the SD root or a Wi-Fi NFS root |
| `onekvm-nanokvm-go-boot` | `watchdog.c`, `watchdog-handoff`, `go-udhcpc` | Boot watchdog feeder and its hand-off; address, routes and DNS for the NFS root |
| `onekvm-machine-nanokvm-go` | `machine.json`, `edid/`, `*.service`, `*.conf`, `99-peripherals.rules`, `grow-root` | Machine identity, EDID presets, systemd units and drop-ins, udev rules, SD root expansion |

## Build tools

Used by the `onekvm-distro` firmware, boot image and SD image recipes. None of them writes to a device.

| Tool | Output |
| --- | --- |
| `package-firmware.py` | ROM `boot.bin` (signed SPL, two copies), `atf.img` (BL31) and `uboot.bin` |
| `package-boot.py` | Axera `kernel.img` (AArch64 Image with initramfs) and `dtb.img`; rejects images that would overlap BL31 or the DTB |
| `build-sd.py` | Two-partition SD image; checks the firmware and boot files against their manifests |

## SD card layout

| Partition | Content |
| --- | --- |
| p1, FAT32, 128 MiB, label `ONEKVMBOOT` | `boot.bin`, `uboot.bin`, `atf.img`, `kernel.img`, `dtb.img`, and the `onekvm/` settings folder |
| p2, ext4, from sector 264192 | Root filesystem. The first SD boot grows it to the end of the card |

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
