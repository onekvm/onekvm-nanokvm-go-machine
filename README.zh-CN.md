# onekvm-nanokvm-go-machine

[English](README.md) | 简体中文

Sipeed NanoKVM Go（AX620Q，ARM64）的机型文件，由 `onekvm-distro` 的
`meta-onekvm-nanokvm-go` 构建。

状态：适配中。AArch64 SPL/BL31/U-Boot 已在一台 Go 上从保留原厂 FAT 格式的 SD 卡冷启动；
固件 manifest 仍为 `hardware_qualified: false`。

## 软件包

| 软件包 | 文件 | 内容 |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init` | initramfs 的 `/init`：启动硬件 watchdog、显示启动 logo 并让启动进度条持续前进，然后把 SD 的根分区槽挂在 userdata overlay 下，或挂载 Wi-Fi NFS 根目录 |
| `onekvm-boot-splash` | `boot-splash/` | 屏幕上的启动 logo、启动进度条及其动画进程，供 initramfs、根系统和屏幕服务调用 |
| `onekvm-boot-progress` | `boot-splash/systemd/` | 根系统启动期间推进启动进度条的 systemd 单元 |
| `onekvm-nanokvm-go-boot` | `watchdog.c`、`watchdog-handoff`、`go-udhcpc` | 启动阶段 watchdog 喂狗程序及其交接；NFS 根目录的地址、路由和 DNS |
| `onekvm-machine-nanokvm-go` | `machine.json`、`edid/`、`*.service`、`*.conf`、`99-peripherals.rules`、`grow-root` | 机型身份、EDID 预设、systemd 单元和 drop-in、udev 规则、单根分区 SD 卡的根分区扩容 |

## 构建工具

供 `onekvm-distro` 的固件、启动镜像和 SD 镜像配方调用，都不会写入设备。

| 工具 | 产物 |
| --- | --- |
| `package-firmware.py` | ROM 读取的 `boot.bin`（签名 SPL，两份）、`atf.img`（BL31）和 `uboot.bin` |
| `package-boot.py` | Axera 格式的 `kernel.img`（含 initramfs 的 AArch64 Image）和 `dtb.img`；会与 BL31 或 DTB 重叠的镜像直接拒绝 |
| `build-sd.py` | A/B SD 镜像；按 manifest 核对固件和启动文件，并检查根镜像不超过槽容量 |

## SD 卡布局

槽容量与 NanoKVM Pro SD 相同，扇区为 512 字节：

| 分区 | 起始扇区 | 内容 |
| --- | ---: | --- |
| MBR | 0 | 只有分区表。BootROM 和 SPL 从第一个 FAT 分区读取 `boot.bin` 及后续文件 |
| p1，FAT32，128 MiB，活动分区，卷标 `ONEKVMBOOT` | 2048 | `boot.bin`、`uboot.bin`、`atf.img`、`kernel-a.img`/`dtb-a.img`、`kernel-b.img`/`dtb-b.img`、`boot-slot` 和 `onekvm/` 设置目录 |
| p2，128 MiB | 264192 | 只读根分区 A，SquashFS |
| p3，128 MiB | 526336 | 只读根分区 B，SquashFS；镜像在两个槽写入同一份根 |
| p4，userdata | 788480 | 首次从 SD 启动时建到卡尾并格式化为 f2fs；已有的 f2fs 或 ext4 userdata 保留 |

`boot-slot` 的内容是 `A` 或 `B`。U-Boot 加载该槽的内核和 DTB，缺失或无效时改用另一槽，
并把 `rauc.slot=` 传给 Linux。initramfs 以只读方式挂载该槽的根，上面叠一层 overlay，
`upper` 和 `work` 目录在 userdata 上，两个槽共用；`/mnt/storage` 是 userdata 的 `storage`
目录。挂载前 initramfs 会删除 upper 中属于新镜像的文件副本（同 NanoKVM Pro），避免旧副本
遮住镜像里的新版本。userdata 无法挂载时 Go 仍能启动，改动只保留到下次重启。没有 `boot-slot` 的卡
沿用旧布局：FAT 上是 `kernel.img`、`dtb.img`，p2 是 ext4 根分区并扩展到整张卡。

## Wi-Fi NFS 根目录

FAT 分区上没有 `onekvm/nfs-root` 时，Go 从 SD 根目录启动。要通过 Wi-Fi 从 NFS
启动，在 `onekvm/` 目录放两个文件：

| 文件 | 内容 |
| --- | --- |
| `nfs-root` | 一行 `<服务器 IP>:/绝对导出路径`，仅支持 IPv4，例如 `192.0.2.10:/srv/nfs/onekvm/nanokvm-go` |
| `wpa_supplicant.conf` | 该 Wi-Fi 网络的 wpa_supplicant 配置 |

根目录以 NFSv3 over TCP（`hard,nolock`）挂载，导出必须允许 root 写入。
Wi-Fi 连接由 initramfs 建立并保持到关机，系统运行时不要修改这条 Wi-Fi 连接。
NFS 根目录不会扩容。

## Watchdog

initramfs 中任何启动失败都会等待硬件 watchdog 复位，不会停住。启动阶段喂狗最多
240 秒，期间须进入运行时 watchdog；运行时 watchdog 在 `onekvm.service` 启动前接管。
OneKVM 的重启同样通过 5 秒硬件 watchdog 复位完成。

## EDID

| 预设 | 模式 |
| --- | --- |
| `factory.edid.bin` | 默认，与 `1080p60.edid.bin` 相同 |
| `1080p60.edid.bin` | 1920×1080 @ 60 Hz |
| `720p60.edid.bin` | 1280×720 @ 60 Hz |

## 测试

`make test` 检查启动输入（`package-boot.py`）、启动 logo 绘制、启动进度条及其从 initramfs 到根系统的交接。

## 许可证

GPL-2.0-only，见 `LICENSE`。带 `SPDX-License-Identifier: MIT` 头的文件（如
`watchdog.c` 和 `boot-splash/`）为 MIT，见 `LICENSES/MIT.txt`。
