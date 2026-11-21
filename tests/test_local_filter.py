"""Unit tests for the cross-platform local blank-cell pre-filter."""

import io
import unittest
from PIL import Image, ImageDraw

from handwriting_grader.local_filter import is_blank_cell


class TestLocalBlankFilter(unittest.TestCase):
    def _to_png_bytes(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_pure_white_cell_with_border_is_blank(self):
        img = Image.new("RGB", (200, 80), "white")
        draw = ImageDraw.Draw(img)
        # Draw dark outer table border (should be ignored by inset margin)
        draw.rectangle([0, 0, 199, 79], outline="black", width=3)
        self.assertTrue(is_blank_cell(self._to_png_bytes(img)))

    def test_cell_with_handwriting_is_not_blank(self):
        img = Image.new("RGB", (200, 80), "white")
        draw = ImageDraw.Draw(img)
        draw.line([(40, 40), (120, 40)], fill="black", width=3)
        self.assertFalse(is_blank_cell(self._to_png_bytes(img)))

    def test_corrupt_bytes_fallback_to_false(self):
        self.assertFalse(is_blank_cell(b"not-an-image"))


if __name__ == "__main__":
    unittest.main()
