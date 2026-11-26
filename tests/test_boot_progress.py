"""The initramfs and root-system units hand the boot bar on in checkpoint order."""
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
UNITS = ROOT / "boot-splash" / "systemd"


def go_checkpoints():
    source = (ROOT / "boot-splash" / "onekvm-boot-splash.c").read_text()
    table = source[source.index("boot_stages[] = {"):]
    go = table[table.index("#ifdef ONEKVM_SPLASH_GO"):table.index("#else")]
    return re.findall(r'\{ "([a-z]+)", (\d+), (\d+) \}', go)


def init_lines():
    return (ROOT / "init").read_text().splitlines()


class Checkpoints(unittest.TestCase):
    def test_go_checkpoints_follow_the_boot(self):
        names = [name for name, _, _ in go_checkpoints()]
        self.assertEqual(names, ["initramfs", "storage", "early", "sysinit", "basic", "screen", "done"])
        percents = [int(percent) for _, percent, _ in go_checkpoints()]
        # U-Boot draws 2% and 4% before Linux.
        self.assertGreater(percents[0], 4)
        self.assertEqual(percents, sorted(set(percents)))
        self.assertEqual(percents[-1], 100)

    def test_units_mark_root_checkpoints_without_holding_up_targets(self):
        self.assertEqual(sorted(path.name for path in UNITS.iterdir()), [
            "onekvm-boot-progress-basic.service",
            "onekvm-boot-progress-early.service",
            "onekvm-boot-progress-sysinit.service",
        ])
        for stage in ("early", "sysinit", "basic"):
            lines = (UNITS / f"onekvm-boot-progress-{stage}.service").read_text().splitlines()
            self.assertIn(f"ExecStart=-/usr/sbin/onekvm-boot-splash progress {stage}", lines)
            self.assertIn("Before=onekvm-device-screen-display.service", lines)
            self.assertIn("DefaultDependencies=no", lines)
            self.assertIn("RemainAfterExit=yes", lines)
            self.assertFalse([line for line in lines if re.match(r"Before=.*\.target", line)])
        self.assertIn("After=sysinit.target onekvm-boot-progress-early.service",
                      (UNITS / "onekvm-boot-progress-sysinit.service").read_text().splitlines())
        self.assertIn("After=basic.target onekvm-boot-progress-sysinit.service",
                      (UNITS / "onekvm-boot-progress-basic.service").read_text().splitlines())


class InitHandOff(unittest.TestCase):
    def run_splash(self, modprobe_status):
        lines = init_lines()
        start = lines.index("splash=")
        end = lines.index("fi", start)
        storage = next(line for line in lines if "progress storage" in line)
        script = "\n".join(lines[start:end + 1] + ["wait", storage]) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            tools, calls = Path(temp) / "bin", Path(temp) / "calls"
            tools.mkdir()
            for name, body in (("onekvm-boot-splash", f'printf "%s\\n" "$*" >>"{calls}"'),
                               ("modprobe", f"exit {modprobe_status}")):
                path = tools / name
                path.write_text(f"#!/bin/sh\n{body}\n")
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            subprocess.run(["/bin/sh", "-c", script], check=True,
                           env={"PATH": f"{tools}:{os.environ['PATH']}"})
            return calls.read_text().splitlines() if calls.exists() else []

    def test_boot_screen_starts_the_animator_and_marks_storage(self):
        self.assertEqual(self.run_splash(0), ["--progress initramfs boot", "animate", "progress storage"])

    def test_no_panel_means_no_bar_calls(self):
        self.assertEqual(self.run_splash(1), [])

    def test_storage_is_marked_before_dev_moves_to_the_new_root(self):
        lines = init_lines()
        storage = next(i for i, line in enumerate(lines) if "progress storage" in line)
        self.assertLess(lines.index("umount /boot-config"), storage)
        self.assertLess(storage, next(i for i, line in enumerate(lines) if 'mount --move "/$dir"' in line))
        self.assertLess(storage, next(i for i, line in enumerate(lines) if "exec switch_root" in line))


if __name__ == "__main__":
    unittest.main()
