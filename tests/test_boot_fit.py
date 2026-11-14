"""Build slot FITs the mainline U-Boot accepts, and refuse inputs it would reject."""
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("go_fit", Path(__file__).parents[1] / "package-fit.py")
fit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)


def dtb(compatible=(b"sipeed,nanokvm-go", b"axera,ax620q"), size=0):
    """A minimal flattened DT: a root node with compatible, plus padding."""
    strings = b"model\0compatible\0"
    model = b"test\0"
    value = b"".join(s + b"\0" for s in compatible)

    def prop(name_offset, data):
        return struct.pack(">3I", 3, len(data), name_offset) + data + bytes(-len(data) % 4)

    body = struct.pack(">I", 1) + bytes(4) + prop(0, model) + prop(6, value)
    body += struct.pack(">2I", 2, 9)  # FDT_END_NODE, FDT_END
    header = 40 + 16  # header and an empty reservation map
    blob = struct.pack(">10I", 0xD00DFEED, header + len(body) + len(strings) + size, header,
                       header + len(body), 40, 17, 16, 0, len(strings), len(body))
    return blob + bytes(16) + body + strings + bytes(size)


def image(size=64, image_size=None):
    """An AArch64 Image header; image_size includes the kernel's BSS."""
    data = bytearray(size)
    struct.pack_into("<3Q", data, 8, 0, image_size or size, 8)
    data[56:60] = b"ARM\x64"
    return bytes(data)


class Fit(unittest.TestCase):
    def package(self, kernel=None, tree=None, fit_size=64):
        seen = {}

        def mkimage(command, cwd, check, stdout):
            work = Path(cwd)
            seen["its"] = (work / command[2]).read_text()
            seen["kernel"] = gzip.decompress((work / "Image.gz").read_bytes())
            seen["dtb"] = (work / "go.dtb").read_bytes()
            Path(command[3]).write_bytes(bytes.fromhex("d00dfeed") + bytes(fit_size - 4))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Image").write_bytes(kernel or image())
            (root / "go.dtb").write_bytes(tree or dtb())
            with patch.object(fit.subprocess, "run", side_effect=mkimage):
                manifest = fit.package(root / "Image", root / "go.dtb", root / "out")
            self.assertEqual(json.loads((root / "out/manifest.json").read_text()), manifest)
            self.assertEqual(sorted(p.name for p in (root / "out").iterdir()), ["boot.itb", "manifest.json"])
        return manifest, seen

    def test_wraps_the_kernel_and_dt_for_the_go_load_addresses(self):
        manifest, seen = self.package()
        self.assertEqual(manifest["format"], "fit")
        self.assertEqual(manifest["architecture"], "arm64")
        self.assertEqual(manifest["boot.itb"]["size"], 64)
        self.assertEqual(seen["kernel"], image())
        self.assertEqual(seen["dtb"], dtb())
        for line in ('load = <0x40200000>;', 'entry = <0x40200000>;', 'compression = "gzip";',
                     'algo = "sha256";', 'default = "go";'):
            self.assertIn(line, seen["its"])
        self.assertEqual(seen["its"].count('algo = "sha256";'), 2)

    def test_reads_the_root_compatible(self):
        self.assertEqual(fit.root_compatible(dtb()), ["sipeed,nanokvm-go", "axera,ax620q"])

    def test_rejects_a_dt_for_another_board(self):
        with self.assertRaises(ValueError):
            self.package(tree=dtb(compatible=(b"sipeed,nanokvm-pro",)))

    def test_rejects_a_dt_without_room_for_fixups(self):
        with self.assertRaises(ValueError):
            self.package(tree=dtb(size=fit.DTB_LIMIT))

    def test_rejects_an_image_over_the_dt(self):
        with self.assertRaises(ValueError):
            self.package(kernel=image(image_size=47 << 20))

    def test_rejects_a_fit_past_the_load_window(self):
        with self.assertRaises(ValueError):
            self.package(fit_size=fit.FIT_LIMIT + 4)

    @unittest.skipUnless(shutil.which("mkimage") and shutil.which("dtc"), "needs mkimage and dtc")
    def test_mkimage_lists_both_hashed_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Image").write_bytes(image(4096))
            (root / "go.dtb").write_bytes(dtb())
            fit.package(root / "Image", root / "go.dtb", root / "out")
            listing = subprocess.run(["mkimage", "-l", str(root / "out/boot.itb")], check=True,
                                     capture_output=True, text=True).stdout
        self.assertIn("Load Address: 0x40200000", listing)
        self.assertIn("Compression:  gzip compressed", listing)
        self.assertEqual(listing.count("Hash algo:    sha256"), 2)
        self.assertIn("Default Configuration: 'go'", listing)


if __name__ == "__main__":
    unittest.main()
