"""Keep the SD layout readable by BootROM/SPL FatFs and the A/B initramfs."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("go_sd", Path(__file__).parents[1] / "build-sd.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)


class Layout(unittest.TestCase):
    def test_mbr_keeps_boot_area_and_slot_geometry(self):
        table = sd.mbr()
        self.assertEqual(table[:440], bytes(440))
        self.assertEqual(table[440:444], bytes.fromhex("47564b4f"))
        self.assertEqual(table[510:], b"\x55\xaa")
        entries = [struct.unpack_from("<B3sB3sII", table, 446 + n * 16) for n in range(4)]
        self.assertEqual([(e[0], e[2], e[4], e[5]) for e in entries], [
            (0x80, 0x0C, 2048, 262144),
            (0, 0x83, 264192, 262144),
            (0, 0x83, 526336, 262144),
            (0, 0, 0, 0),
        ])
        self.assertEqual(sd.USERDATA_START, 788480)

    def root(self, directory, magic, size):
        path = Path(directory) / "root"
        with path.open("wb") as f:
            f.truncate(size)
            f.seek(1024)
            f.write(struct.pack("<I", magic))
        return path

    def test_root_must_be_erofs_within_one_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            sd.check_root(self.root(temp, sd.EROFS_MAGIC, 128 << 20))
            with self.assertRaises(ValueError):
                sd.check_root(self.root(temp, 0xEF53, 4096))
            with self.assertRaises(ValueError):
                sd.check_root(self.root(temp, sd.EROFS_MAGIC, (128 << 20) + 512))

    def test_fat_holds_both_slots_and_selects_a(self):
        copied = {}
        mkfs = []

        def run(command, check):
            if command[0] == "mcopy":
                copied[command[-1]] = Path(command[-2]).read_bytes()
            elif command[0] == "mkfs.vfat":
                mkfs.extend(command)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*sd.FIRMWARE, "kernel.img", "dtb.img"):
                (root / name).write_bytes(name.encode())
            with patch.object(sd.subprocess, "run", side_effect=run):
                sd.boot_fat(root / "boot.fat", root, root)
        self.assertEqual(sorted(copied), sorted([
            "::boot.bin", "::atf.img", "::uboot.bin", "::kernel-a.img", "::dtb-a.img",
            "::kernel-b.img", "::dtb-b.img", "::boot-slot"]))
        self.assertEqual(copied["::boot-slot"], b"A\n")
        self.assertEqual(mkfs[mkfs.index("-h") + 1], str(sd.BOOT_START))
        self.assertEqual(copied["::kernel-b.img"], b"kernel.img")


if __name__ == "__main__":
    unittest.main()
