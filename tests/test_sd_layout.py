"""Keep the SD layout readable by BootROM/SPL FatFs and the A/B initramfs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
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
            with self.assertRaises(ValueError):
                sd.check_root(self.root(temp, sd.SQUASHFS_MAGIC, (64 << 20) + 512))

    def boot_fat(self, images, names):
        copied = []
        mkfs = []

        def run(command, check=False):
            if command[0] == "mcopy":
                copied.append((command[-1], Path(command[-2]).read_bytes()))
            elif command[0] == "mkfs.vfat":
                mkfs.extend(command)
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*sd.FIRMWARE, *names, "root"):
                (root / name).write_bytes(name.encode())
            with patch.object(sd.subprocess, "run", side_effect=run):
                sd.boot_fat(root / "boot.fat", root, root, images, root / "root")
        files = dict(copied)
        self.assertEqual(files["::boot-slot"], b"A\n")
        self.assertIn("::" + sd.INSTALL_MARKER, files)
        # The image ends with the root file.
        self.assertEqual(copied[-1], ("::onekvm/root.squashfs", b"root"))
        self.assertEqual(mkfs[mkfs.index("-h") + 1], str(sd.BOOT_START))
        return files

    def test_fat_holds_capsule_slot_a_root_and_asks_to_install(self):
        copied = self.boot_fat({"architecture": "arm64"}, ("kernel.img", "dtb.img"))
        self.assertEqual(sorted(copied), sorted([
            "::boot.bin", "::atf.img", "::uboot.bin", "::kernel-a.img", "::dtb-a.img",
            "::boot-slot", "::onekvm/install-b", "::onekvm/root.squashfs"]))
        self.assertEqual(copied["::kernel-a.img"], b"kernel.img")

    def test_fat_holds_fit_slot_a_root_and_asks_to_install(self):
        copied = self.boot_fat({"format": "fit", "architecture": "arm64"}, ("boot.itb",))
        self.assertEqual(sorted(copied), sorted([
            "::boot.bin", "::atf.img", "::uboot.bin", "::boot-a.itb", "::boot-slot",
            "::onekvm/install-b", "::onekvm/root.squashfs"]))
        self.assertEqual(copied["::boot-a.itb"], b"boot.itb")

    def test_a_root_that_does_not_fit_on_the_fat_is_rejected(self):
        def run(command, check=False):
            full = command[0] == "mcopy" and command[-1] == "::" + sd.ROOT_FILE
            return subprocess.CompletedProcess(command, 1 if full else 0)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (*sd.FIRMWARE, "boot.itb", "root"):
                (root / name).write_bytes(name.encode())
            with patch.object(sd.subprocess, "run", side_effect=run), self.assertRaises(ValueError):
                sd.boot_fat(root / "boot.fat", root, root, {"format": "fit"}, root / "root")

    def fat32(self, path, last_cluster):
        """A FAT32 image: 512-byte sectors, 2-sector clusters, 32 reserved sectors, two 8-sector FATs."""
        boot = bytearray(512)
        struct.pack_into("<HBHB", boot, 11, 512, 2, 32, 2)
        struct.pack_into("<I", boot, 36, 8)
        table = bytearray(8 * 512)
        struct.pack_into("<III", table, 0, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)
        # A chain ending in last_cluster, with a free cluster before it.
        struct.pack_into("<I", table, 4 * (last_cluster - 2), last_cluster)
        struct.pack_into("<I", table, 4 * last_cluster, 0x0FFFFFFF)
        with path.open("wb") as f:
            f.truncate(1 << 20)
            f.write(boot)
            f.seek(32 * 512)
            f.write(table)

    def test_fat_end_is_the_end_of_the_last_allocated_cluster(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "boot.fat"
            self.fat32(path, 100)
            # Data area at sector 32 + 2 * 8; clusters 2..100 take 99 * 2 sectors.
            self.assertEqual(sd.fat_end(path), (48 + 99 * 2) * 512)

    def test_image_holds_the_mbr_and_the_used_fat_only(self):
        def fat(path, firmware, boot, images, rootfs):
            self.fat32(path, 300)
            with path.open("r+b") as f:
                f.seek(sd.fat_end(path) - 3)
                f.write(b"END")
                f.write(b"free space")

        with tempfile.TemporaryDirectory() as temp:
            root = self.root(temp, sd.SQUASHFS_MAGIC, 3 << 20)
            output = Path(temp) / "sd.img"
            argv = ["build-sd.py", "--firmware", temp, "--boot", temp, "--rootfs", str(root),
                    "--output", str(output)]
            with patch.object(sd, "check_inputs", return_value={"format": "fit"}), \
                    patch.object(sd, "boot_fat", side_effect=fat), patch("sys.argv", argv):
                sd.main()
            image = output.read_bytes()
        self.assertEqual(len(image), sd.BOOT_START * 512 + (48 + 299 * 2) * 512)
        self.assertEqual(image[:512], sd.mbr())
        self.assertEqual(image[sd.BOOT_START * 512 + 11:sd.BOOT_START * 512 + 13], struct.pack("<H", 512))
        self.assertTrue(image.endswith(b"END"))

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
