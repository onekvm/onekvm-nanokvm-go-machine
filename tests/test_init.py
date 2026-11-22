"""Run the initramfs byte reader against the markers build-sd.py and mkfs write."""
import importlib.util
import os
import re
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
class FirstBootInstall(unittest.TestCase):
    """install_roots and install_slot_b on files: p2/p3 and the boot FAT replaced by temporary paths."""

    def run_init(self, temp, function):
        tools = Path(temp) / "tools"
        tools.mkdir(exist_ok=True)
        for applet in ("hexdump", "dd", "wc", "cp", "mv", "rm", "sha256sum", "sync"):
            if not (tools / applet).exists():
                os.symlink(shutil.which("busybox"), tools / applet)
        body = init_function(function)
        for device, variable in (("/dev/mmcblk1p", "${DEV}p"), ("/boot-config", "$BOOT"),
                                 ("/proc/sys/vm/drop_caches", "$DROP")):
            body = body.replace(device, variable)
        script = "\n".join((init_function("bytes_hex"), init_function("squashfs_bytes"), body,
                            'root_file="$BOOT/onekvm/root.squashfs"', "ab_layout() { :; }", "mount() { :; }",
                            function))
        env = {"PATH": str(tools), "DEV": str(Path(temp) / "mmcblk1"), "BOOT": str(Path(temp) / "boot"),
               "DROP": str(Path(temp) / "drop")}
        return subprocess.run([shutil.which("busybox"), "sh", "-c", script], env=env,
                              capture_output=True, text=True).returncode

    def card(self, temp, root_file=None, boot_files=()):
        for part in "23":
            Path(temp, "mmcblk1p" + part).write_bytes(b"\xaa" * (6 << 20))
        boot = Path(temp) / "boot"
        (boot / "onekvm").mkdir(parents=True)
        (boot / "onekvm" / "install-b").write_text("install\n")
        if root_file is not None:
            (boot / "onekvm" / "root.squashfs").write_bytes(root_file)
        for name, data in dict(boot_files).items():
            (boot / name).write_bytes(data)
        return boot

    def squashfs(self, size, used):
        data = bytearray(os.urandom(size))
        data[:4] = b"hsqs"
        struct.pack_into("<Q", data, 40, used)
        return bytes(data)

    def test_writes_the_root_file_to_both_slots_then_deletes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.squashfs((3 << 20) + 4096, (3 << 20) + 5)
            boot = self.card(temp, root)
            self.assertEqual(self.run_init(temp, "install_roots"), 0)
            for part in "23":
                # On files dd also truncates; the card's partitions keep their size.
                self.assertEqual(Path(temp, "mmcblk1p" + part).read_bytes()[:len(root)], root)
            self.assertFalse((boot / "onekvm" / "root.squashfs").exists())
            self.assertTrue((boot / "onekvm" / "install-b").exists())
            self.assertEqual(Path(temp, "drop").read_text(), "3\n")

    def test_keeps_the_slots_and_the_file_unless_it_is_padded_squashfs(self):
        for root in (bytes(1 << 20), self.squashfs((1 << 20) + 512, 1 << 20)):
            with tempfile.TemporaryDirectory() as temp:
                boot = self.card(temp, root)
                self.assertNotEqual(self.run_init(temp, "install_roots"), 0)
                self.assertTrue((boot / "onekvm" / "root.squashfs").exists())
                for part in "23":
                    self.assertEqual(Path(temp, "mmcblk1p" + part).read_bytes(), b"\xaa" * (6 << 20))

    def test_copies_slot_a_fit_into_slot_b_then_clears_the_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            fit = os.urandom(100000)
            boot = self.card(temp, boot_files={"boot-a.itb": fit})
            self.assertEqual(self.run_init(temp, "install_slot_b"), 0)
            self.assertEqual((boot / "boot-b.itb").read_bytes(), fit)
            self.assertFalse((boot / "onekvm" / "install-b").exists())
            self.assertEqual(sorted(p.name for p in boot.iterdir()), ["boot-a.itb", "boot-b.itb", "onekvm"])

    def test_copies_the_capsule_pair_of_the_vendor_u_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            boot = self.card(temp, boot_files={"kernel-a.img": b"kernel", "dtb-a.img": b"dtb"})
            self.assertEqual(self.run_init(temp, "install_slot_b"), 0)
            self.assertEqual((boot / "kernel-b.img").read_bytes(), b"kernel")
            self.assertEqual((boot / "dtb-b.img").read_bytes(), b"dtb")
            self.assertFalse((boot / "boot-b.itb").exists())
            self.assertFalse((boot / "onekvm" / "install-b").exists())


class InitramfsBusyBox(unittest.TestCase):
    """The initramfs BusyBox (OE defconfig) lacks options a host BusyBox has."""

    def test_init_uses_only_the_dd_operands_and_head_options_it_has(self):
        text = (ROOT / "init").read_text()
        for command in re.findall(r"\bdd ([^|;\n]*)", text):
            for operand in re.findall(r"\b(\w+)=", command):
                self.assertIn(operand, ("if", "of", "bs", "count", "skip", "seek"), command)
        self.assertNotRegex(text, r"\bhead -c")


if __name__ == "__main__":
    unittest.main()
