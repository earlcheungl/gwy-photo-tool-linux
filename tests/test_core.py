"""Regression tests for the Linux port (run: .venv/bin/python tests/test_core.py)."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwy_photo.config import Params, load_params  # noqa: E402
from gwy_photo.core import (  # noqa: E402
    MARKER,
    background_patches,
    classify_hsi,
    crop_geometry,
    encode_output_jpeg,
    is_jpeg_file,
    load_cascade,
    process_image,
)

CASCADE = load_cascade(ROOT / "assets" / "haarcascade_frontalface_alt2.xml")


def solid(w, h, bgr):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = bgr
    return img


class IniTests(unittest.TestCase):
    def test_shipped_ini_overrides(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        self.assertEqual(p.s_min, 0.05)
        self.assertEqual(p.s_max, 0.1)
        self.assertEqual(p.i_min, 0.1)
        self.assertEqual(p.i_max, 0.7)
        self.assertEqual(p.dst_width, 295)
        self.assertEqual(p.dst_height, 413)

    def test_code_defaults(self):
        p = Params()
        self.assertEqual(p.s_max, 0.3)
        self.assertEqual(p.i_max, 0.9)


class BackgroundTests(unittest.TestCase):
    def test_white(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        kind, patches = background_patches(solid(600, 800, (255, 255, 255)), p)
        self.assertEqual(kind, 0)
        self.assertTrue(all(pt.kind == 0 for pt in patches))

    def test_blue(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        # BGR blue
        kind, patches = background_patches(solid(600, 800, (200, 120, 60)), p)
        self.assertEqual(kind, 1)
        for pt in patches:
            self.assertTrue(p.blue_h_min <= pt.h <= p.blue_h_max)

    def test_green_is_rejected(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        kind, _ = background_patches(solid(600, 800, (40, 180, 40)), p)
        self.assertEqual(kind, 3)

    def test_classify_white_requires_bright(self):
        p = Params()
        self.assertEqual(classify_hsi(0.0, 0.0, 0.95, p), 0)
        self.assertEqual(classify_hsi(0.0, 0.0, 0.85, p), 3)


class GeometryTests(unittest.TestCase):
    def test_golden_ratio_crop(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        rect, ratios = crop_geometry((200, 150, 240, 240), 600, 800, p)
        self.assertIsNotNone(rect)
        x, y, w, h = rect
        self.assertEqual(w, int(240 * p.crop_scale))
        self.assertEqual(h, int(w * p.dst_height / p.dst_width + 0.5))
        self.assertAlmostEqual((x + w / 2), 320, delta=1)

    def test_head_too_small(self):
        p = Params()
        rect, ratios = crop_geometry((10, 10, 50, 60), 600, 800, p)
        self.assertIsNone(rect)
        self.assertLess(ratios["head_ratio"], 0.32)


class PipelineTests(unittest.TestCase):
    def test_blank_white_has_no_face(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        res = process_image(solid(600, 800, (255, 255, 255)), p, CASCADE)
        self.assertEqual(res.status, 5)

    def test_low_pixels(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        res = process_image(solid(200, 200, (255, 255, 255)), p, CASCADE)
        self.assertEqual(res.status, 2)

    def test_save_marker_and_size(self):
        p = load_params(ROOT / "assets" / "photo.ini")
        img = solid(295, 413, (250, 250, 250))
        data = encode_output_jpeg(img, p)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertTrue(data.endswith(MARKER))
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (295, 413))
        # The original CxImage output uses a JFIF header with 96x96 dpi.
        self.assertEqual(im.info.get("jfif_unit"), 1)
        self.assertEqual(im.info.get("jfif_density"), (96, 96))

    def test_not_jpeg(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            name = f.name
        self.assertFalse(is_jpeg_file(name))
        Path(name).unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
