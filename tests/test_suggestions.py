"""Unit tests for rubric-required region suggestions and editor state."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handwriting_grader.regions import prepare_regions
from handwriting_grader.rubric import write_rubric
from handwriting_grader.suggestions import generate_suggestions


class TestSuggestionsRubricRequirement(unittest.TestCase):
    def test_generate_suggestions_requires_rubric(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf = root / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            roster = root / "roster.xlsx"
            roster.write_bytes(b"")
            marking = root / "marking.docx"
            marking.write_bytes(b"")
            config_path = root / "exam.yaml"
            config = {
                "exam": {
                    "name": "Test",
                    "roster": str(roster),
                    "roster_sheet": "Sheet1",
                    "scanned_pdf": str(pdf),
                    "marking_scheme": str(marking),
                    "output_dir": str(root / "output"),
                },
                "layout": {"pages_per_student": 1, "same_layout_for_all": True},
            }
            model_cfg = root / "config.yaml"
            model_cfg.write_text(
                "vertex_ai:\n  use_vertexai: true\nmodels:\n  annotation: gemini-4-argon\n",
                encoding="utf-8",
            )
            with patch("handwriting_grader.suggestions.verify_run", return_value=(root / "output" / "regions", {"pages": [{"width": 100, "height": 100}], "config_sha256": "a", "pdf_sha256": "b"})):
                with self.assertRaises(ValueError) as ctx:
                    generate_suggestions(config, root, config_path, model_cfg, "proj", "global")
                self.assertIn("marking rubric question list", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
