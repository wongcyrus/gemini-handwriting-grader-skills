import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from handwriting_grader.cache import CachedGrader, cache_directory, enabled_agents


class Provider:
    def __init__(self):
        self.calls = []

    def ocr(self, image, field):
        self.calls.append(("ocr", field))
        return "answer"

    def grade(self, question, answer):
        self.calls.append(("grading", answer))
        return {"mark": 1, "similarity_score": 0.5, "reasoning": "example"}

    def moderate(self, question, entries):
        self.calls.append(("moderation", len(entries)))
        return {entry["script_index"]: {"script_index": entry["script_index"], "moderated_mark": 1,
                                        "flag": False, "note": ""} for entry in entries}


class CacheTests(unittest.TestCase):
    def test_provider_errors_do_not_trigger_duplicate_requests(self):
        class FailingProvider:
            def __init__(self):
                self.ocr_calls = 0
                self.grade_calls = 0

            def ocr(self, image, field, candidates=None):
                self.ocr_calls += 1
                raise TypeError("provider OCR failure")

            def grade(self, question, answer="", image=None):
                self.grade_calls += 1
                raise ValueError("provider grading failure")

        with tempfile.TemporaryDirectory() as temporary:
            provider = FailingProvider()
            cached = CachedGrader(provider, Path(temporary), [], {}, "endpoint", "v1")
            with self.assertRaisesRegex(TypeError, "provider OCR failure"):
                cached.ocr(b"image", "ID", candidates=["001"])
            with self.assertRaisesRegex(ValueError, "provider grading failure"):
                cached.grade({"marks": 2}, image=b"image")
            self.assertEqual((provider.ocr_calls, provider.grade_calls), (1, 1))
            from handwriting_grader.grading import batch_extract_metadata
            with patch("handwriting_grader.grading.crop_image", return_value=b"image"):
                result = batch_extract_metadata(
                    provider, [0], ["ID"], {"ID": (0, {})}, 1, Path(temporary),
                    metadata_candidates={"ID": ["001"]},
                )
            self.assertEqual(provider.ocr_calls, 2)
            self.assertEqual(result[0]["errors"], [{"field": "ID", "type": "TypeError"}])

    def test_only_enabled_agents_are_cached_and_reused_across_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "models.yaml"
            config.write_text(yaml.safe_dump({"caching": {"enabled": True, "agents": {
                "ocr": True, "grading": True, "moderation": False,
            }}}))
            agents = enabled_agents(config)
            self.assertEqual(agents, ["ocr", "grading"])
            models = {"ocr": "o", "grading": "g", "moderation": "m"}
            first = Provider()
            cached = CachedGrader(first, root / "cache", agents, models, "endpoint", "api_version")
            question = {"question_number": "1", "question_text": "Question", "marking_scheme": "Scheme", "marks": 2}
            entries = [{"script_index": 0, "answer": "answer", "mark": 1, "reasoning": "example"}]
            cached.ocr(b"image", "answer")
            cached.grade(question, "answer")
            cached.moderate(question, entries)
            second = Provider()
            resumed = CachedGrader(second, root / "cache", agents, models, "endpoint", "api_version")
            self.assertEqual(resumed.ocr(b"image", "answer"), "answer")
            self.assertEqual(resumed.grade(question, "answer")["mark"], 1)
            resumed.moderate(question, entries)
            self.assertEqual(second.calls, [("moderation", 1)])
            resumed.ocr(b"different", "answer")
            self.assertEqual(second.calls[-1], ("ocr", "answer"))
            files = list((root / "cache").glob("*.json"))
            path = next(p for p in files if json.loads(p.read_text())["key"]["kind"] == "grading")
            invalid = json.loads(path.read_text())
            invalid["result"]["mark"] = 999
            path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(ValueError, "Invalid cached grade"):
                resumed.grade(question, "answer")

    def test_caching_is_off_without_explicit_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "models.yaml"
            config.write_text("{}")
            self.assertEqual(enabled_agents(config), [])

    def test_shared_cache_requires_explicit_private_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "models.yaml"
            config.write_text(yaml.safe_dump({"caching": {"enabled": True, "scope": "shared",
                                                         "directory": "shared", "agents": {"ocr": True}}}))
            shared = cache_directory(config, root / "run")
            self.assertEqual(shared, root / "shared" / "handwriting-grader-cache-v1")
            models = {"ocr": "o", "grading": "g", "moderation": "m"}
            first = Provider()
            CachedGrader(first, shared, enabled_agents(config), models, "p", "l").ocr(b"image", "answer")
            second = Provider()
            CachedGrader(second, cache_directory(config, root / "other"), enabled_agents(config),
                         models, "p", "l").ocr(b"image", "answer")
            self.assertEqual(second.calls, [])
            if os.name != "nt":
                shared.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "private"):
                    CachedGrader(second, shared, ["ocr"], models, "p", "l")
            config.write_text(yaml.safe_dump({"caching": {"scope": "shared"}}))
            with self.assertRaisesRegex(ValueError, "caching.directory"):
                cache_directory(config, root / "run")

    def test_grade_question_batch_caching(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "models.yaml"
            config.write_text(yaml.safe_dump({"caching": {"enabled": True, "agents": {
                "batch_grading": True,
            }}}))
            agents = enabled_agents(config)
            self.assertEqual(agents, ["batch_grading"])
            models = {"batch_grading": "bg-model", "grading": "g"}

            class BatchProvider:
                def __init__(self):
                    self.calls = 0

                def grade_question_batch(self, question, script_items):
                    self.calls += 1
                    return {
                        item["script_index"]: {
                            "script_index": item["script_index"],
                            "answer": "Test answer",
                            "mark": 2.0,
                            "similarity_score": 0.9,
                            "reasoning": "Well done",
                            "moderator_flag": False,
                            "moderator_note": "",
                        }
                        for item in script_items
                    }

            first = BatchProvider()
            cached = CachedGrader(first, root / "cache", agents, models, "endpoint", "api_version")
            q = {"question_number": "1", "question_text": "Q", "marking_scheme": "S", "marks": 2}
            scripts = [{"script_index": 0, "image": b"fake_png_data"}]
            res1 = cached.grade_question_batch(q, scripts)
            self.assertEqual(res1.grades[0].mark, 2.0)
            self.assertEqual(first.calls, 1)

            second = BatchProvider()
            resumed = CachedGrader(second, root / "cache", agents, models, "endpoint", "api_version")
            res2 = resumed.grade_question_batch(q, scripts)
            self.assertEqual(res2.grades[0].mark, 2.0)
            self.assertEqual(second.calls, 0)


if __name__ == "__main__":
    unittest.main()
