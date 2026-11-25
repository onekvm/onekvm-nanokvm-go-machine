# onekvm-nanokvm-go-machine

[English](README.md) | 简体中文

Sipeed NanoKVM Go（AX620Q，ARM64）的机型文件，由 `onekvm-distro` 的
`meta-sipeed/meta-nanokvm-go` 构建。

状态：适配中。AArch64 SPL/BL31/U-Boot 已在一台 Go 上从保留原厂 FAT 格式的 SD 卡冷启动；
固件 manifest 仍为 `hardware_qualified: false`。

## 软件包

| 软件包 | 文件 | 内容 |
| --- | --- | --- |
| `onekvm-nanokvm-go-init` | `init` | initramfs 的 `/init`：启动硬件 watchdog、显示启动 logo 并让启动进度条持续前进，执行共用的 Recovery 检查，然后把 SD 的根分区槽挂在 userdata overlay 下，或挂载 Wi-Fi NFS 根目录 |
| `onekvm-boot-splash` | `boot-splash/` | 屏幕上的启动 logo、启动进度条及其动画进程，供 initramfs、根系统和屏幕服务调用 |
| `onekvm-boot-progress` | `boot-splash/systemd/` | 根系统启动期间推进启动进度条的 systemd 单元 |
| `onekvm-nanokvm-go-boot` | `watchdog.c`、`watchdog-handoff`、`go-udhcpc` | 启动阶段 watchdog 喂狗程序及其交接；NFS 根目录的地址、路由和 DNS |
| `onekvm-machine-nanokvm-go` | `machine.json`、`edid/`、`*.service`、`*.conf`、`99-peripherals.rules`、`grow-root` | 机型身份、EDID 预设、systemd 单元和 drop-in、udev 规则、单根分区 SD 卡的根分区扩容 |

## 构建工具

供 `onekvm-distro` 的固件、启动镜像和 SD 镜像配方调用，都不会写入设备。

| 工具 | 产物 |
| --- | --- |
| `package-firmware.py` | ROM 读取的 `boot.bin`（签名 SPL，两份）、`atf.img`（BL31）和 `uboot.bin` |
| `package-fit.py` | 主线 U-Boot 用的 `boot.itb`：一个 FIT，含 AArch64 Image（含 initramfs，gzip 压缩）和 Go DTB，各带 SHA-256 哈希，配置里记有镜像的 build ID；拒绝 `package-boot.py` 会拒绝的镜像、不是 Go 的 DTB 和超出 U-Boot 载入区的 FIT |
| `package-boot.py` | 厂商 U-Boot 用的 Axera 格式 `kernel.img`（含 initramfs 的 AArch64 Image）和 `dtb.img`；会与 BL31 或 DTB 重叠的镜像直接拒绝 |
| `build-sd.py` | 由任一种启动格式生成 A/B SD 镜像；按 manifest 核对固件和启动文件，并检查根镜像不超过槽容量和 FAT 剩余空间 |

## SD 卡布局

槽容量与 NanoKVM Pro SD 相同，扇区为 512 字节：

| 分区 | 起始扇区 | 内容 |
| --- | ---: | --- |
| MBR | 0 | 只有分区表。BootROM 和 SPL 从第一个 FAT 分区读取 `boot.bin` 及后续文件 |
| p1，FAT32，128 MiB，活动分区，卷标 `ONEKVMBOOT` | 2048 | `boot.bin`、`uboot.bin`、`atf.img`、两个槽的启动镜像、`boot-slot` 和 `onekvm/` 设置目录。SD 镜像只带槽 A 的启动镜像和待安装的根镜像 |
| p2，128 MiB | 264192 | 只读根分区 A，SquashFS；SD 镜像不包含此分区，见下文 |
| p3，128 MiB | 526336 | 只读根分区 B，SquashFS；SD 镜像不包含此分区，见下文 |
| p4，userdata | 788480 | 首次从 SD 启动时建到卡尾并格式化为 f2fs；已有的 f2fs 或 ext4 userdata 保留 |

各槽的启动镜像取决于 U-Boot：

| U-Boot | 槽 A | 槽 B |
| --- | --- | --- |
| 主线 | `boot-a.itb` | `boot-b.itb` |
| 厂商 | `kernel-a.img`、`dtb-a.img` | `kernel-b.img`、`dtb-b.img` |

`boot-slot` 的内容是 `A` 或 `B`。U-Boot 加载该槽的启动镜像，缺失或无效时改用另一槽，
并把 `rauc.slot=` 和 FIT 里的 `onekvm.build-id=` 传给 Linux。更新装入的槽先试启动：
`boot-trial/tries` 记着剩余次数，用完后 U-Boot 改启另一槽；该槽通过健康检查后 Linux 删除这个文件。initramfs 以只读方式挂载该槽的根，上面叠一层 overlay，
`upper` 和 `work` 目录在 userdata 上，两个槽共用；`/mnt/storage` 是 userdata 的 `storage`
目录。挂载前 initramfs 会删除 upper 中属于新镜像的文件副本（同 NanoKVM Pro），避免旧副本
遮住镜像里的新版本。userdata 无法挂载时 Go 仍能启动，改动只保留到下次重启。

SD 镜像只有分区表和 FAT 的已用部分，写入时不碰两个根分区和 userdata。根镜像是 FAT 上的文件 `onekvm/root.squashfs`：
从槽 A 首次启动时显示安装界面，把它写入 p2 和 p3，从卡上回读校验后删除该文件；成功前槽 A 不会启动。随后按
`onekvm/install-b` 把槽 A 的启动镜像复制为槽 B 的，回读校验后删除该文件；这一步失败时槽 A 照常启动，但没有可回退的槽。
安装中断时下次启动重做。更新写入槽 B 时也会删除 `onekvm/install-b`。根镜像须能和启动镜像一起放进 FAT，约 110 MiB 以内。

没有 `boot-slot` 的卡
沿用旧布局：FAT 上是 `boot.itb`（主线 U-Boot）或 `kernel.img`、`dtb.img`（厂商 U-Boot），p2 是 ext4
根分区并扩展到整张卡。

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

## Recovery

挂载任何 SD 分区之前，initramfs 先运行 `onekvm-nanokvm-recovery` 的 Recovery hook。
开机按住 BOOT、在系统设置中点 Recovery、命令行带 `onekvm_recovery=1`，或连续 10 次
启动都没有正常运行满 2 分钟时进入 Recovery；请求恢复出厂设置时也在这里执行。新卡先
创建 userdata 分区，因为 Recovery 会校验全部 4 个分区。NFS 启动从不清零计数，因此不
计数。Recovery 把整张 SD 卡导出为 USB 大容量存储并提供 ACM 控制台（UART3 上也有），
屏幕提示把镜像写入该磁盘。控制台中执行 `reboot` 即可重启。检查失败时继续正常启动。

## Watchdog

initramfs 中任何启动失败都会等待硬件 watchdog 复位，不会停住。启动阶段喂狗最多
240 秒，期间须进入运行时 watchdog；运行时 watchdog 在 `onekvm.service` 启动前接管。
Recovery 中持续喂狗，不受该时限约束。
OneKVM 的重启同样通过 5 秒硬件 watchdog 复位完成。

## EDID

| 预设 | 模式 |
| --- | --- |
| `factory.edid.bin` | 默认 1920×1080 @ 60 Hz，所有机型是同一个文件（`onekvm-distro` 的 `onekvm-machine-files.inc`） |
| `1080p60.edid.bin` | 1920×1080 @ 60 Hz，来自 `onekvm-distro` |
| `720p60.edid.bin` | 1280×720 @ 60 Hz |

## 测试

`make test` 检查启动输入（`package-fit.py`、`package-boot.py`）、SD 布局、启动 logo 绘制、启动进度条及其从 initramfs 到根系统的交接。

## 许可证

GPL-2.0-only，见 `LICENSE`。带 `SPDX-License-Identifier: MIT` 头的文件（如
`watchdog.c` 和 `boot-splash/`）为 MIT，见 `LICENSES/MIT.txt`。
