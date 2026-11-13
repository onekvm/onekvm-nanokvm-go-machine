# onekvm-nanokvm-go-machine

[English](README.md) | 简体中文

Sipeed NanoKVM Go（AX620Q，ARM64）的机型文件，由 `onekvm-distro` 的
`meta-onekvm-nanokvm-go` 构建。

状态：适配中。AArch64 SPL/BL31/U-Boot 尚未通过实机验证（固件 manifest 中
`hardware_qualified: false`）。

## 软件包

| 软件包 | 文件 | 内容 |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init`、`boot-splash/` | initramfs 的 `/init`：启动硬件 watchdog、显示启动 logo，然后挂载 SD 根目录或 Wi-Fi NFS 根目录 |
| `onekvm-nanokvm-go-boot` | `watchdog.c`、`watchdog-handoff`、`go-udhcpc` | 启动阶段 watchdog 喂狗程序及其交接；NFS 根目录的地址、路由和 DNS |
| `onekvm-machine-nanokvm-go` | `machine.json`、`edid/`、`*.service`、`*.conf`、`99-peripherals.rules`、`grow-root` | 机型身份、EDID 预设、systemd 单元和 drop-in、udev 规则、SD 根分区扩容 |

## 构建工具

供 `onekvm-distro` 的固件、启动镜像和 SD 镜像配方调用，都不会写入设备。

| 工具 | 产物 |
| --- | --- |
| `package-firmware.py` | ROM 读取的 `boot.bin`（签名 SPL，两份）、`atf.img`（BL31）和 `uboot.bin` |
| `package-boot.py` | Axera 格式的 `kernel.img`（含 initramfs 的 AArch64 Image）和 `dtb.img`；会与 BL31 或 DTB 重叠的镜像直接拒绝 |
| `build-sd.py` | 两分区 SD 镜像；按 manifest 核对固件和启动文件 |

## SD 卡布局

| 分区 | 内容 |
| --- | --- |
| p1，FAT32，128 MiB，卷标 `ONEKVMBOOT` | `boot.bin`、`uboot.bin`、`atf.img`、`kernel.img`、`dtb.img` 和 `onekvm/` 设置目录 |
| p2，ext4，起始扇区 264192 | 根文件系统，首次从 SD 启动时扩展到整张卡 |

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

`make test` 检查启动输入（`package-boot.py`）和启动 logo 绘制。

## 许可证

GPL-2.0-only，见 `LICENSE`。带 `SPDX-License-Identifier: MIT` 头的文件（如
`watchdog.c` 和 `boot-splash/`）为 MIT，见 `LICENSES/MIT.txt`。
