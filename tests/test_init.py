"""Run the initramfs byte reader against the markers build-sd.py and mkfs write."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("go_sd", ROOT / "build-sd.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)


def init_function(name):
    lines = (ROOT / "init").read_text().splitlines()
    start = lines.index(name + "() {")
    end = lines.index("}", start)
    return "\n".join(lines[start:end + 1])


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox, as in the initramfs")
class BytesHex(unittest.TestCase):
    def read(self, path, offset, length):
        with tempfile.TemporaryDirectory() as tools:
            # Only the BusyBox hexdump applet: the initramfs od lacks -A/-t/-j/-N.
            os.symlink(shutil.which("busybox"), Path(tools) / "hexdump")
            script = init_function("bytes_hex") + '\nbytes_hex "$1" "$2" "$3"\n'
            return subprocess.run([shutil.which("busybox"), "sh", "-c", script, "sh", str(path), str(offset), str(length)],
                                  env={"PATH": tools}, capture_output=True, text=True, check=True).stdout

    def test_reads_disk_id_and_superblock_magics(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "disk"
            data = bytearray(4096)
            data[:512] = sd.mbr()
            image.write_bytes(data)
            self.assertEqual(self.read(image, 440, 4), "47564b4f")
            struct.pack_into("<I", data, 1024, 0xF2F52010)
            struct.pack_into("<H", data, 1080, 0xEF53)
            image.write_bytes(data)
            self.assertEqual(self.read(image, 1024, 4), "1020f5f2")
            self.assertEqual(self.read(image, 1080, 2), "53ef")



@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox, as in the initramfs")
class InstallSlotB(unittest.TestCase):
    """install_slot_b on files: p2/p3 and the boot FAT replaced by temporary paths."""

    def run_install(self, temp):
        tools = Path(temp) / "tools"
        tools.mkdir()
        for applet in ("hexdump", "dd", "cp", "mv", "rm", "sha256sum", "sync"):
            os.symlink(shutil.which("busybox"), tools / applet)
        install = init_function("install_slot_b")
        for device, variable in (("/dev/mmcblk1p2", "$P2"), ("/dev/mmcblk1p3", "$P3"),
                                 ("/boot-config", "$BOOT"), ("/proc/sys/vm/drop_caches", "$DROP")):
            install = install.replace(device, variable)
        script = "\n".join((init_function("bytes_hex"), init_function("squashfs_bytes"), install,
                            "ab_layout() { :; }", "mount() { :; }", "install_slot_b"))
        env = {"PATH": str(tools), "P2": str(Path(temp) / "p2"), "P3": str(Path(temp) / "p3"),
               "BOOT": str(Path(temp) / "boot"), "DROP": str(Path(temp) / "drop")}
        return subprocess.run([shutil.which("busybox"), "sh", "-c", script], env=env,
                              capture_output=True, text=True).returncode

    def card(self, temp, root_a, boot_files):
        Path(temp, "p2").write_bytes(root_a)
        Path(temp, "p3").write_bytes(b"\xaa" * (6 << 20))
        boot = Path(temp) / "boot"
        (boot / "onekvm").mkdir(parents=True)
        (boot / "onekvm" / "install-b").write_text("install\n")
        for name, data in boot_files.items():
            (boot / name).write_bytes(data)
        return boot

    def squashfs(self, used):
        data = bytearray(os.urandom(6 << 20))
        data[:4] = b"hsqs"
        struct.pack_into("<Q", data, 40, used)
        return bytes(data)

    def test_copies_root_a_and_its_boot_image_then_clears_the_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root_a = self.squashfs((3 << 20) + 5)
            fit = os.urandom(100000)
            boot = self.card(temp, root_a, {"boot-a.itb": fit})
            self.assertEqual(self.run_install(temp), 0)
            # Whole MiB blocks covering bytes_used.
            self.assertEqual(Path(temp, "p3").read_bytes()[:4 << 20], root_a[:4 << 20])
            self.assertEqual((boot / "boot-b.itb").read_bytes(), fit)
            self.assertFalse((boot / "onekvm" / "install-b").exists())
            self.assertEqual(sorted(p.name for p in boot.iterdir()), ["boot-a.itb", "boot-b.itb", "onekvm"])
            self.assertEqual(Path(temp, "drop").read_text(), "3\n")

    def test_copies_the_capsule_pair_of_the_vendor_u_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            boot = self.card(temp, self.squashfs(1 << 20), {"kernel-a.img": b"kernel", "dtb-a.img": b"dtb"})
            self.assertEqual(self.run_install(temp), 0)
            self.assertEqual((boot / "kernel-b.img").read_bytes(), b"kernel")
            self.assertEqual((boot / "dtb-b.img").read_bytes(), b"dtb")
            self.assertFalse((boot / "boot-b.itb").exists())

    def test_keeps_the_marker_and_slot_b_without_a_squashfs_root_a(self):
        with tempfile.TemporaryDirectory() as temp:
            boot = self.card(temp, bytes(6 << 20), {"boot-a.itb": b"fit"})
            self.assertNotEqual(self.run_install(temp), 0)
            self.assertTrue((boot / "onekvm" / "install-b").exists())
            self.assertFalse((boot / "boot-b.itb").exists())
            self.assertEqual(Path(temp, "p3").read_bytes(), b"\xaa" * (6 << 20))

if __name__ == "__main__":
    unittest.main()
