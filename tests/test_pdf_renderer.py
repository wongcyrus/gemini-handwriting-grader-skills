"""Unit tests for cross-platform PDF rendering."""

import tempfile
import unittest
from pathlib import Path
from PIL import Image

from handwriting_grader.pdf_renderer import (
    is_pypdfium2_available,
    render_pdf_to_images,
)


class TestPdfRenderer(unittest.TestCase):
    def test_pypdfium2_is_installed(self):
        self.assertTrue(is_pypdfium2_available())

    def test_render_pdf_to_images(self):
        sample_pdf = Path("examples/notebook-sample/VTC Test.pdf")
        if not sample_pdf.exists():
            self.skipTest("Sample PDF not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pages = render_pdf_to_images(
                pdf_path=sample_pdf,
                output_dir=tmp_path,
                prefix="testpage",
                dpi=200,
                first_page=1,
                last_page=2,
            )
            self.assertEqual(len(pages), 2)
            self.assertTrue(pages[0].exists())
            self.assertTrue(pages[1].exists())

            with Image.open(pages[0]) as img:
                self.assertEqual(img.size, (1654, 2338))
                self.assertEqual(img.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
