#!/usr/bin/env python3
"""Wrap the OE-built Go Image+initramfs and DTB in one FIT for the mainline U-Boot.

U-Boot reads a slot's FIT to 0x44400000, checks every image hash and the Go DT,
decompresses the kernel to 0x40200000 and places the DT below 0x43100000. It
passes the configuration's onekvm,build-id to Linux, where slot health compares
it with the root's build ID.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

# U-Boot reads the FIT to 0x44400000 and stops at the kernel DT's 12 KiB
# crash@45ffd000; its own memory lies above that, below 0x48000000.
FIT_LIMIT = 0x45FFD000 - 0x44400000
# Same DT room as dtb.img: the DT and 64 KiB of fixups stay below 0x43100000.
DTB_LIMIT = 1024 * 1024 - 65536
COMPATIBLE = "sipeed,nanokvm-go"
# U-Boot drops an ID it would not pass safely on the kernel command line.
BUILD_ID = re.compile(r"[A-Za-z0-9._-]{1,96}")

ITS = """/dts-v1/;

/ {
	description = "OneKVM NanoKVM Go boot slot";
	#address-cells = <1>;

	images {
		kernel {
			description = "Linux with initramfs";
			data = /incbin/("Image.gz");
			type = "kernel";
			arch = "arm64";
			os = "linux";
			compression = "gzip";
			load = <0x40200000>;
			entry = <0x40200000>;
			hash {
				algo = "sha256";
			};
		};

		fdt {
			description = "NanoKVM Go";
			data = /incbin/("go.dtb");
			type = "flat_dt";
			arch = "arm64";
			compression = "none";
			hash {
				algo = "sha256";
			};
		};
	};

	configurations {
		default = "go";

		go {
			description = "NanoKVM Go";
			kernel = "kernel";
			fdt = "fdt";
			onekvm,build-id = "@BUILD_ID@";
		};
	};
};
"""


def boot_checks():
    spec = importlib.util.spec_from_file_location("go_boot", Path(__file__).with_name("package-boot.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def root_compatible(dtb):
    """The root node's compatible strings."""
    magic, total, structure, strings = struct.unpack_from(">4I", dtb)
    if magic != 0xD00DFEED or total > len(dtb):
        raise ValueError("invalid DTB")
    offset = structure
    if struct.unpack_from(">I", dtb, offset)[0] != 1:  # FDT_BEGIN_NODE of the root
        raise ValueError("DTB has no root node")
    offset = (dtb.index(b"\0", offset + 4) + 4) & ~3
    while True:
        token = struct.unpack_from(">I", dtb, offset)[0]
        if token == 4:  # FDT_NOP
            offset += 4
        elif token == 3:  # FDT_PROP
            length, name = struct.unpack_from(">2I", dtb, offset + 4)
            end = dtb.index(b"\0", strings + name)
            if dtb[strings + name:end] == b"compatible":
                value = dtb[offset + 12:offset + 12 + length]
                return [s.decode() for s in value.split(b"\0") if s]
            offset = (offset + 12 + length + 3) & ~3
        else:  # a child node or the end of the root: no compatible
            return []


def package(kernel_path, dtb_path, out, build_id, mkimage="mkimage"):
    if not BUILD_ID.fullmatch(build_id):
        raise ValueError("build ID must be 1-96 characters of A-Z, a-z, 0-9, '.', '_' or '-'")
    boot = boot_checks()
    kernel = kernel_path.read_bytes()
    dtb = dtb_path.read_bytes()
    boot.check_kernel(kernel)
    boot.check_dtb(dtb)
    if len(dtb) > DTB_LIMIT:
        raise ValueError("DTB leaves no room for U-Boot fixups below 0x43100000")
    if COMPATIBLE not in root_compatible(dtb):
        raise ValueError("DTB is not for a NanoKVM Go; U-Boot would reject the slot")
    out.mkdir(parents=True, exist_ok=True)
    fit = out / "boot.itb"
    with tempfile.TemporaryDirectory(dir=out) as temp:
        work = Path(temp)
        # mtime 0 keeps the FIT reproducible; mkimage takes SOURCE_DATE_EPOCH.
        (work / "Image.gz").write_bytes(gzip.compress(kernel, 9, mtime=0))
        (work / "go.dtb").write_bytes(dtb)
        (work / "boot.its").write_text(ITS.replace("@BUILD_ID@", build_id))
        subprocess.run([mkimage, "-f", "boot.its", str(fit.resolve())], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
    data = fit.read_bytes()
    if data[:4] != bytes.fromhex("d00dfeed"):
        raise ValueError("mkimage did not write a FIT")
    if len(data) > FIT_LIMIT:
        raise ValueError("FIT exceeds the U-Boot load window")
    manifest = {
        "format": "fit", "architecture": "arm64", "build-id": build_id,
        "boot.itb": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "kernel": {"size": len(kernel), "sha256": hashlib.sha256(kernel).hexdigest()},
        "dtb": {"size": len(dtb), "sha256": hashlib.sha256(dtb).hexdigest()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("kernel", "dtb", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--build-id", required=True)
    p.add_argument("--mkimage", default="mkimage")
    a = p.parse_args()
    package(a.kernel, a.dtb, a.out, a.build_id, a.mkimage)


if __name__ == "__main__":
    main()
