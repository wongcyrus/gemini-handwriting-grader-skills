import math
import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw

from handwriting_grader.alignment import (
    _prepare_ink_map,
    align_page_file,
    check_scan_orientation_and_sequence,
    estimate_rigid_transform,
    warp_image_to_reference,
)
from handwriting_grader.cli import main


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

    def test_outlier_quadrant_heavy_handwriting_is_rejected(self):
        ref = _make_form_image()
        shifted = ref.transform(
            ref.size, Image.Transform.AFFINE, (1, 0, -6, 0, 1, -14),
            resample=Image.Resampling.BILINEAR, fillcolor=(255, 255, 255),
        )
        # Add heavy scribble/diagram in bottom-left quadrant only
        draw = ImageDraw.Draw(shifted)
        for y in range(550, 950, 12):
            draw.line([20, y, 360, y + 30], fill=(0, 0, 0), width=4)
        tx, ty, _ = estimate_rigid_transform(_prepare_ink_map(ref), _prepare_ink_map(shifted), *ref.size)
        self.assertAlmostEqual(tx, -6.0, delta=2.5)
        self.assertAlmostEqual(ty, -14.0, delta=2.5)

    def test_conflicting_answer_panels_do_not_move_a_correct_id_crop(self):
        ref = _make_form_image()
        target = ref.copy()
        target.paste("white", (0, 500, 800, 1000))
        target.paste(ref.crop((0, 500, 400, 1000)), (100, 420))
        target.paste(ref.crop((400, 500, 800, 1000)), (400, 620))
        id_box = (480, 40, 740, 90)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "original.jpg"
            output = Path(tmp) / "aligned.jpg"
            target.save(source, format="JPEG", quality=95)
            modified, transform = align_page_file(
                source, _prepare_ink_map(ref), ref.size, output_path=output,
            )
            self.assertFalse(modified)
            self.assertEqual(transform, (0.0, 0.0, 0.0))
            with Image.open(source) as original, Image.open(output) as aligned:
                np.testing.assert_array_equal(
                    np.asarray(original.crop(id_box)), np.asarray(aligned.crop(id_box)),
                )

    def test_check_scan_orientation_and_sequence_flags_upside_down_swapped_and_rotated(self):
        p0 = _make_form_image()
        p1 = Image.new("RGB", (800, 1000), (255, 255, 255))
        d1 = ImageDraw.Draw(p1)
        d1.ellipse([100, 120, 350, 300], outline=(0, 0, 0), width=4)
        d1.rectangle([450, 500, 720, 820], outline=(0, 0, 0), width=4)
        d1.text((120, 600), "PAGE TWO", fill=(0, 0, 0))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reg_img = root / "regions" / "images"
            reg_img.mkdir(parents=True)
            p0.save(reg_img / "0.jpg", format="JPEG", quality=95)
            p1.save(reg_img / "1.jpg", format="JPEG", quality=95)

            pages_dir = root / "pages"
            pages_dir.mkdir()
            # Script 1: valid (p0, p1)
            p0.save(pages_dir / "0.jpg", format="JPEG", quality=95)
            p1.save(pages_dir / "1.jpg", format="JPEG", quality=95)
            # Script 2: p0 is upside-down 180 deg, p1 is rotated 90 deg (landscape)
            p0.rotate(180).save(pages_dir / "2.jpg", format="JPEG", quality=95)
            p1.rotate(90, expand=True).save(pages_dir / "3.jpg", format="JPEG", quality=95)
            # Script 3: swapped sequence (p1, p0)
            p1.save(pages_dir / "4.jpg", format="JPEG", quality=95)
            p0.save(pages_dir / "5.jpg", format="JPEG", quality=95)

            valid_issues = check_scan_orientation_and_sequence(
                [pages_dir / "0.jpg", pages_dir / "1.jpg"], root / "regions", 2
            )
            self.assertEqual(valid_issues, [])

            bad_issues = check_scan_orientation_and_sequence(
                [pages_dir / f"{i}.jpg" for i in range(6)], root / "regions", 2
            )
            self.assertEqual(len(bad_issues), 4)
            self.assertIn("upside-down", bad_issues[0])
            self.assertIn("rotated 90°/270°", bad_issues[1])
            self.assertIn("out of sequence", bad_issues[2])
            self.assertIn("out of sequence", bad_issues[3])

    def test_consensus_flags_upside_down_first_script_without_regions(self):
        p0 = _make_form_image()
        p1 = Image.new("RGB", (800, 1000), (255, 255, 255))
        d1 = ImageDraw.Draw(p1)
        d1.ellipse([100, 120, 350, 300], outline=(0, 0, 0), width=4)
        d1.rectangle([450, 500, 720, 820], outline=(0, 0, 0), width=4)
        d1.text((120, 600), "PAGE TWO", fill=(0, 0, 0))

        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp)
            # Script 1 has page 1 upside-down (180 deg)
            p0.rotate(180).save(pages_dir / "0.jpg", format="JPEG", quality=95)
            p1.save(pages_dir / "1.jpg", format="JPEG", quality=95)
            # Scripts 2 & 3 are normal upright
            for s in (1, 2):
                p0.save(pages_dir / f"{s * 2}.jpg", format="JPEG", quality=95)
                p1.save(pages_dir / f"{s * 2 + 1}.jpg", format="JPEG", quality=95)

            issues = check_scan_orientation_and_sequence(
                [pages_dir / f"{i}.jpg" for i in range(6)], None, 2
            )
            self.assertEqual(len(issues), 1)
            self.assertIn("PDF page 1 (Script #1, page 1)", issues[0])
            self.assertIn("upside-down", issues[0])

    def test_cli_help_includes_check_scans(self):
        from unittest.mock import patch
        with patch("sys.stdout"), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                main(["check-scans", "--help"])
            self.assertEqual(cm.exception.code, 0)
