import math
import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw

from handwriting_grader.alignment import (
    _prepare_ink_map,
    align_page_file,
    estimate_rigid_transform,
    warp_image_to_reference,
)


def _make_form_image(size=(800, 1000)) -> Image.Image:
    im = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(im)
    # Draw distinct printed landmarks in all four quadrants
    draw.rectangle([40, 40, 320, 90], outline=(0, 0, 0), width=3)
    draw.text((55, 55), "HEADER LEFT FORM", fill=(0, 0, 0))
    draw.rectangle([480, 40, 740, 90], outline=(0, 0, 0), width=3)
    draw.text((500, 55), "ID: 12345678", fill=(0, 0, 0))
    draw.rectangle([40, 700, 320, 880], outline=(0, 0, 0), width=3)
    draw.line([40, 750, 320, 820], fill=(0, 0, 0), width=3)
    draw.rectangle([480, 700, 740, 880], outline=(0, 0, 0), width=3)
    draw.line([480, 820, 740, 750], fill=(0, 0, 0), width=3)
    return im


class TestPageAlignment(unittest.TestCase):
    def test_recovers_translation_and_warps_back(self):
        ref = _make_form_image()
        # Shift target DOWN by 18px and RIGHT by 10px (inverse warp needs tx=-10, ty=-18)
        shifted = ref.transform(
            ref.size, Image.Transform.AFFINE, (1, 0, -10, 0, 1, -18),
            resample=Image.Resampling.BILINEAR, fillcolor=(255, 255, 255),
        )
        ref_arr = _prepare_ink_map(ref)
        cur_arr = _prepare_ink_map(shifted)
        tx, ty, theta = estimate_rigid_transform(ref_arr, cur_arr, *ref.size)
        self.assertAlmostEqual(tx, -10.0, delta=2.5)
        self.assertAlmostEqual(ty, -18.0, delta=2.5)
        self.assertLess(abs(math.degrees(theta)), 0.3)

        aligned = warp_image_to_reference(shifted, tx, ty, theta)
        res_tx, res_ty, _ = estimate_rigid_transform(ref_arr, _prepare_ink_map(aligned), *ref.size)
        self.assertLess(abs(res_tx), 2.0)
        self.assertLess(abs(res_ty), 2.0)

    def test_recovers_small_rotation_and_translation(self):
        ref = _make_form_image()
        angle = math.radians(1.0)
        w, h = ref.size
        cx, cy = w / 2.0, h / 2.0
        cos_t, sin_t = math.cos(-angle), math.sin(-angle)
        a, b = cos_t, -sin_t
        c = cx - a * cx - b * cy - 8.0
        d, e = sin_t, cos_t
        f = cy - d * cx - e * cy - 12.0
        skewed = ref.transform(
            ref.size, Image.Transform.AFFINE, (a, b, c, d, e, f),
            resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255),
        )
        ref_arr = _prepare_ink_map(ref)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skewed.jpg"
            skewed.save(p, format="JPEG", quality=95)
            modified, _ = align_page_file(p, ref_arr, (w, h))
            self.assertTrue(modified)
            with Image.open(p) as aligned:
                res_tx, res_ty, res_th = estimate_rigid_transform(ref_arr, _prepare_ink_map(aligned), w, h)
        self.assertLess(abs(res_tx), 2.5)
        self.assertLess(abs(res_ty), 2.5)
        self.assertLess(abs(math.degrees(res_th)), 0.25)

    def test_blank_page_is_no_op(self):
        blank = Image.new("RGB", (400, 500), (255, 255, 255))
        arr = _prepare_ink_map(blank)
        tx, ty, theta = estimate_rigid_transform(arr, arr, 400, 500)
        self.assertEqual((tx, ty, theta), (0.0, 0.0, 0.0))

    def test_align_page_file_roundtrip(self):
        ref = _make_form_image()
        ref_arr = _prepare_ink_map(ref)
        shifted = ref.transform(
            ref.size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, -20),
            resample=Image.Resampling.BILINEAR, fillcolor=(255, 255, 255),
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "page.jpg"
            shifted.save(p, format="JPEG", quality=95)
            modified, (tx, ty, _) = align_page_file(p, ref_arr, ref.size)
            self.assertTrue(modified)
            self.assertAlmostEqual(ty, -20.0, delta=2.5)
