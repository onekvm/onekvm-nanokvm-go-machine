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


if __name__ == "__main__":
    unittest.main()
