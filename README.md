# onekvm-nanokvm-go-machine

Machine files for the Sipeed NanoKVM Go (AX620Q, ARM64), built by
`onekvm-distro` (`meta-onekvm-nanokvm-go`).

| Path | Content |
| --- | --- |
| `init` | Initramfs entry: SD root, or Wi-Fi NFS root configured from the SD boot partition |
| `watchdog.c`, `watchdog-handoff` | Boot-time hardware watchdog feeder and its hand-off to the runtime watchdog |
| `go-udhcpc`, `nfs-network.service` | Address, routes and DNS for the NFS root |
| `*.service`, `*.conf`, `99-peripherals.rules` | systemd units, drop-ins and udev rules installed by the machine package |
| `machine.json`, `edid/` | Machine identity and EDID presets |
| `boot-splash/` | Early boot logo, built with `-DONEKVM_SPLASH_GO` |
| `package-boot.py`, `package-firmware.py`, `build-sd.py` | Boot package, firmware package and SD image assembly |

`make test` runs the boot input checks and the splash test.
