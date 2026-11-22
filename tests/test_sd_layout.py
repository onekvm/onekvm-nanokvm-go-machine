"""Keep the SD layout readable by BootROM/SPL FatFs and the A/B initramfs."""
import hashlib
import importlib.util
import json
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

    def root(self, directory, magic, size, offset=0):
        path = Path(directory) / "root"
        with path.open("wb") as f:
            f.truncate(size)
            f.seek(offset)
            f.write(struct.pack("<I", magic))
        return path

    def test_root_must_be_squashfs_within_one_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            sd.check_root(self.root(temp, sd.SQUASHFS_MAGIC, 128 << 20))
            with self.assertRaises(ValueError):
                # An EROFS root carries its magic at offset 1024.
                sd.check_root(self.root(temp, 0xE0F5E1E2, 4096, offset=1024))
            with self.assertRaises(ValueError):
                sd.check_root(self.root(temp, sd.SQUASHFS_MAGIC, (128 << 20) + 512))

    def boot_fat(self, images, names):
        copied = {}
        mkfs = []

        def run(command, check):
            if command[0] == "mcopy":
                copied[command[-1]] = Path(command[-2]).read_bytes()
            elif command[0] == "mkfs.vfat":
                mkfs.extend(command)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*sd.FIRMWARE, *names):
                (root / name).write_bytes(name.encode())
            with patch.object(sd.subprocess, "run", side_effect=run):
                sd.boot_fat(root / "boot.fat", root, root, images)
        self.assertEqual(copied["::boot-slot"], b"A\n")
        self.assertIn("::" + sd.INSTALL_MARKER, copied)
        self.assertEqual(mkfs[mkfs.index("-h") + 1], str(sd.BOOT_START))
        return copied

    def test_fat_holds_capsule_slot_a_and_asks_to_install_b(self):
        copied = self.boot_fat({"architecture": "arm64"}, ("kernel.img", "dtb.img"))
        self.assertEqual(sorted(copied), sorted([
            "::boot.bin", "::atf.img", "::uboot.bin", "::kernel-a.img", "::dtb-a.img",
            "::boot-slot", "::onekvm/install-b"]))
        self.assertEqual(copied["::kernel-a.img"], b"kernel.img")

    def test_fat_holds_fit_slot_a_and_asks_to_install_b(self):
        copied = self.boot_fat({"format": "fit", "architecture": "arm64"}, ("boot.itb",))
        self.assertEqual(sorted(copied), sorted([
            "::boot.bin", "::atf.img", "::uboot.bin", "::boot-a.itb", "::boot-slot",
            "::onekvm/install-b"]))
        self.assertEqual(copied["::boot-a.itb"], b"boot.itb")

    def test_image_ends_with_root_a_and_leaves_slot_b_unwritten(self):
        def fat(path, firmware, boot, images):
            with path.open("wb") as f:
                f.truncate(sd.BOOT_SECTORS * 512)
                f.write(b"FAT")

        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp, sd.SQUASHFS_MAGIC, 3 << 20)
            output = Path(temp) / "sd.img"
            argv = ["build-sd.py", "--firmware", temp, "--boot", temp, "--rootfs", str(root),
                    "--output", str(output)]
            with patch.object(sd, "check_inputs", return_value={"format": "fit"}), \
                    patch.object(sd, "boot_fat", side_effect=fat), patch("sys.argv", argv):
                sd.main()
            image = output.read_bytes()
            root_bytes = root.read_bytes()
        self.assertEqual(len(image), sd.ROOT_A * 512 + (3 << 20))
        self.assertLess(len(image), sd.ROOT_B * 512)
        self.assertEqual(image[:512], sd.mbr())
        self.assertEqual(image[sd.BOOT_START * 512:sd.BOOT_START * 512 + 3], b"FAT")
        self.assertEqual(image[sd.ROOT_A * 512:], root_bytes)

    def test_inputs_must_match_their_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            firmware, boot = Path(temp) / "firmware", Path(temp) / "boot"
            firmware.mkdir()
            boot.mkdir()
            digests = {}
            for directory, name in ((firmware, n) for n in sd.FIRMWARE):
                (directory / name).write_bytes(name.encode())
                digests[name] = {"sha256": hashlib.sha256(name.encode()).hexdigest()}
            (firmware / "manifest.json").write_text(json.dumps({"architecture": "arm64", **digests}))
            (boot / "boot.itb").write_bytes(b"fit")
            fit = {"format": "fit", "architecture": "arm64",
                   "boot.itb": {"sha256": hashlib.sha256(b"fit").hexdigest()}}
            (boot / "manifest.json").write_text(json.dumps(fit))
            self.assertEqual(sd.check_inputs(firmware, boot), fit)
            (boot / "boot.itb").write_bytes(b"other")
            with self.assertRaises(ValueError):
                sd.check_inputs(firmware, boot)


if __name__ == "__main__":
    unittest.main()
