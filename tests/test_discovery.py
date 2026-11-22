"""Unit tests for tiered online model discovery and per-step cost allocation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from handwriting_grader.discovery import (
    discover_best_tier_models,
    format_model_cost_summary,
    parse_flash_version,
    parse_reasoning_rank,
    resolve_step_models,
    write_model_config,
)


class TestTieredDiscovery(unittest.TestCase):
    def test_version_and_rank_parsing(self):
        self.assertEqual(parse_flash_version("gemini-3.8-flash"), (3, 8))
        self.assertEqual(parse_flash_version("gemini-3.5-flash"), (3, 5))
        self.assertEqual(parse_flash_version("gemini-4-argon"), (0, 0))

        self.assertEqual(parse_reasoning_rank("gemini-4-argon"), (4, 10))
        self.assertEqual(parse_reasoning_rank("gemini-4-hydrogen"), (4, 8))
        self.assertEqual(parse_reasoning_rank("gemini-3-pro"), (3, 5))
        self.assertEqual(parse_reasoning_rank("gemini-3.8-flash"), (0, 0))

    def test_resolve_step_models(self):
        tier_models = {"flash": "gemini-3.8-flash", "reasoning": "gemini-4-argon"}
        step_models = resolve_step_models(tier_models)
        self.assertEqual(step_models["annotation"], "gemini-4-argon")
        self.assertEqual(step_models["ocr"], "gemini-3.8-flash")
        self.assertEqual(step_models["grading"], "gemini-3.8-flash")
        self.assertEqual(step_models["moderation"], "gemini-4-argon")
        self.assertEqual(step_models["analytics"], "gemini-4-argon")

    def test_write_model_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "config.yaml"
            step_models = {
                "annotation": "gemini-3.8-flash",
                "marking_scheme": "gemini-3.8-flash",
                "ocr": "gemini-3.8-flash",
                "grading": "gemini-3.8-flash",
                "moderation": "gemini-4-argon",
                "analytics": "gemini-4-argon",
            }
            write_model_config(target, "my-proj", "global", step_models)
            self.assertTrue(target.is_file())
            with target.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            self.assertEqual(loaded["vertex_ai"]["project_id"], "my-proj")
            self.assertEqual(loaded["models"]["grading"], "gemini-3.8-flash")
            self.assertEqual(loaded["models"]["moderation"], "gemini-4-argon")

    @patch("handwriting_grader.discovery.genai.Client")
    def test_discover_best_tier_models_mock(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        m1 = MagicMock()
        m1.name = "publishers/google/models/gemini-3.8-flash"
        m2 = MagicMock()
        m2.name = "publishers/google/models/gemini-4-argon"
        mock_client.models.list.return_value = [m1, m2]

        mock_resp = MagicMock()
        mock_resp.text = "pong"
        mock_client.models.generate_content.return_value = mock_resp

        result = discover_best_tier_models("mock-proj", "global", candidate_locations=["global"], probe=True)
        self.assertEqual(result["flash"], "gemini-3.8-flash")
        self.assertEqual(result["reasoning"], "gemini-4-argon")


class TestModelValidation(unittest.TestCase):
    def test_valid_models_pass(self):
        from handwriting_grader.discovery import validate_model_eligibility

        for model in ["gemini-3.8-flash", "gemini-4-argon", "gemini-4-hydrogen"]:
            validate_model_eligibility(model, agent="grading")
            validate_model_eligibility(model, agent="annotation")
        validate_model_eligibility("gemini-embedding-2", agent="embedding")

    def test_non_gemini_models_rejected(self):
        from handwriting_grader.discovery import validate_model_eligibility

        for bad in ["claude-3-5-sonnet", "gpt-4o", "llama-3.1-70b", "gemma-2-9b"]:
            with self.assertRaises(ValueError) as ctx:
                validate_model_eligibility(bad, agent="grading")
            self.assertIn("Only Google Gemini models are supported", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_model_eligibility("text-embedding-004", agent="embedding")
        self.assertIn("Only Google Gemini models are supported", str(ctx.exception))

    def test_image_and_media_generation_models_rejected(self):
        from handwriting_grader.discovery import validate_model_eligibility

        for bad in ["imagen-3.0-generate-002", "gemini-image-gen", "gemini-live", "gemini-tts"]:
            with self.assertRaises(ValueError) as ctx:
                validate_model_eligibility(bad, agent="grading")
            self.assertTrue(
                "Disallowed model" in str(ctx.exception) or "Only Google Gemini models are supported" in str(ctx.exception)
            )

    def test_models_older_than_3_8_rejected(self):
        from handwriting_grader.discovery import validate_model_eligibility

        for bad in [
            "gemini-1.0-pro",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3.0-flash",
            "gemini-3.5-flash",
        ]:
            with self.assertRaises(ValueError) as ctx:
                validate_model_eligibility(bad, agent="grading")
            self.assertIn("Models older than Gemini 3.8 are not supported", str(ctx.exception))

    def test_embedding_model_rejected_for_llm_agents(self):
        from handwriting_grader.discovery import validate_model_eligibility

        with self.assertRaises(ValueError) as ctx:
            validate_model_eligibility("gemini-embedding-2", agent="grading")
        self.assertIn("Embedding models cannot generate content", str(ctx.exception))

    def test_load_model_settings_validates_model(self):
        from handwriting_grader.suggestions import load_model_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "config.yaml"
            cfg.write_text(
                "vertex_ai:\n  use_vertexai: true\nmodels:\n  annotation: gemini-2.5-pro\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_model_settings(cfg, "annotation")
            self.assertIn("Models older than Gemini 3.8 are not supported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
