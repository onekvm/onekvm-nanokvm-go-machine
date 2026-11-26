"""The Go touch calibration puts measured taps on the targets the user aimed at."""
import json
from pathlib import Path
import unittest

MACHINE = json.loads((Path(__file__).parents[1] / "machine.json").read_text())

# The touch controller reports x 0..239 and y 0..295 (touchscreen-size-y 296,
# inverted), but across the 284-pixel panel height y only spans about 0..263.
MAX_X, MAX_Y, PANEL_W, PANEL_H = 239, 295, 240, 284

# Taps on nine crosshairs drawn in the 284x240 landscape view (rotate 90):
# (device x, device y) -> target (x, y).
TARGETS = [
    ((208, 17), (20, 20)), ((211, 133), (142, 20)), ((211, 235), (264, 20)),
    ((117, 17), (20, 120)), ((105, 126), (142, 120)), ((98, 241), (264, 120)),
    ((12, 25), (20, 220)), ((10, 145), (142, 220)), ((13, 257), (264, 220)),
]
# Taps on the password keyboard's Show button (x 222..270, y 6..35).
SHOW_TAPS = [(221, 217), (222, 217), (215, 217), (223, 219), (217, 215),
             (216, 218), (217, 218), (212, 217), (214, 220), (220, 227)]


def landscape(x, y, matrix):
    """Mirror onekvm-screen-display: normalize, apply the matrix, clamp, rotate 90."""
    a, b, c, d, e, f = matrix
    u, v = x / MAX_X, y / MAX_Y
    u, v = a * u + b * v + c, d * u + e * v + f
    px = min(max(round(u * (PANEL_W - 1)), 0), PANEL_W - 1)
    py = min(max(round(v * (PANEL_H - 1)), 0), PANEL_H - 1)
    return py, PANEL_W - 1 - px


class TouchCalibration(unittest.TestCase):
    matrix = MACHINE["screen_display"]["touch_calibration"]

    def test_matrix_shape(self):
        self.assertEqual(len(self.matrix), 6)
        self.assertEqual(self.matrix[:3], [1, 0, 0], "device x already spans the panel width")

    def test_targets_within_8_px_across_the_panel(self):
        for raw, (tx, _) in TARGETS:
            x, _ = landscape(*raw, self.matrix)
            self.assertLessEqual(abs(x - tx), 8, (raw, x, tx))

    def test_show_button_taps_hit_the_button(self):
        for raw in SHOW_TAPS:
            x, y = landscape(*raw, self.matrix)
            self.assertTrue(222 <= x <= 270 and 6 <= y <= 35, (raw, x, y))

    def test_right_edge_is_reachable(self):
        # A tap pressed into the bottom-right corner.
        x, _ = landscape(7, 268, self.matrix)
        self.assertGreaterEqual(x, PANEL_H - 6)

    def test_uncalibrated_mapping_misses_the_right_side(self):
        x, _ = landscape(211, 235, [1, 0, 0, 0, 1, 0])
        self.assertLess(x, 264 - 30)


if __name__ == "__main__":
    unittest.main()
