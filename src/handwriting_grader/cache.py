"""Private cache for explicitly enabled model calls."""

import hashlib
import json
import os
from pathlib import Path

import yaml

from .regions import atomic_json, file_hash


def enabled_agents(model_config_path):
    with model_config_path.open(encoding="utf-8") as file:
        settings = yaml.safe_load(file)
    cache = settings.get("caching", {})
    if not isinstance(cache, dict):
        raise ValueError("caching must be a mapping")
    enabled = cache.get("enabled", False)
    agents = cache.get("agents", {})
    if type(enabled) is not bool or not isinstance(agents, dict):
        raise ValueError("caching.enabled and caching.agents are invalid")
    names = ("ocr", "grading", "moderation", "annotation", "marking_scheme", "analytics")
    if any(key in agents and type(agents[key]) is not bool for key in names):
        raise ValueError("Per-agent cache settings must be booleans")
    return [key for key in names if enabled and agents.get(key, False)]


def cache_directory(model_config_path, run_root):
    with model_config_path.open(encoding="utf-8") as file:
        settings = yaml.safe_load(file)
    cache = settings.get("caching", {})
    if not isinstance(cache, dict):
        raise ValueError("caching must be a mapping")
    scope = cache.get("scope", "run")
    if scope == "run":
        return run_root / "cache"
    if scope != "shared" or not isinstance(cache.get("directory"), str) or not cache["directory"].strip():
        raise ValueError("For shared caching set caching.scope: shared and caching.directory")
    directory = (model_config_path.resolve().parent / Path(cache["directory"]).expanduser()).resolve()
    return directory / "handwriting-grader-cache-v1"


class CachedGrader:
    def __init__(self, provider, root, agents, models, project, location, *, source_files=None):
        self.provider = provider
        self.root = root
        self.agents = set(agents)
        self.models = models
        self.project = project
        self.location = location
        self.code_sha256 = [file_hash(Path(__file__))] + [
            file_hash(Path(__file__).with_name(name)) for name in (source_files or ["grading.py"])
        ]
        if self.agents:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            stat = root.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise ValueError("Cache directory must be owned by the user and private (mode 0700)")

    def call(self, kind, payload, compute, validate):
        if kind not in self.agents:
            return validate(compute())
        key = {"format": 1, "code_sha256": self.code_sha256, "kind": kind, "model": self.models[kind],
               "project": self.project, "location": self.location, "input": payload}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        path = self.root / f"{digest}.json"
        if path.exists():
            with path.open(encoding="utf-8") as file:
                cached = json.load(file)
            if cached.get("key") != key:
                raise ValueError("A grading cache entry is invalid")
            return validate(cached.get("result"))
        result = validate(compute())
        atomic_json(path, {"key": key, "result": result})
        return result

    def ocr(self, image, field):
        def validate(value):
            if not isinstance(value, str):
                raise ValueError("Invalid cached OCR response")
            return value
        return self.call("ocr", {"image_sha256": hashlib.sha256(image).hexdigest(), "field": field},
                         lambda: self.provider.ocr(image, field), validate)

    def grade(self, question, answer="", image=None):
        from .grading import GradeResponse
        import inspect
        import math

        payload = {"question": question}
        if answer:
            payload["answer"] = answer
        if image is not None:
            payload["image_sha256"] = hashlib.sha256(image).hexdigest()

        def validate(value):
            if isinstance(value, dict) and "answer" not in value and "mark" in value:
                value["answer"] = payload.get("answer", "")
            grade = GradeResponse.model_validate(value)
            if (not math.isfinite(grade.mark) or not 0 <= grade.mark <= question["marks"]
                    or not math.isfinite(grade.similarity_score) or not 0 <= grade.similarity_score <= 1):
                raise ValueError("Invalid cached grade")
            return grade.model_dump()

        def compute():
            try:
                sig = inspect.signature(self.provider.grade)
                params = sig.parameters
                if "image" in params and "answer" in params:
                    return self.provider.grade(question, answer=answer, image=image)
                elif "image" in params:
                    return self.provider.grade(question, image=image)
                elif "answer" in params:
                    return self.provider.grade(question, answer or "")
            except (ValueError, TypeError):
                pass
            return self.provider.grade(question, answer or "")

        return self.call("grading", payload, compute, validate)

    def moderate(self, question, entries):
        from .grading import ModerationResponse
        import math

        def validate(value):
            response = ModerationResponse.model_validate({"items": value})
            expected = {entry["script_index"] for entry in entries}
            actual = [item.script_index for item in response.items]
            if (len(actual) != len(expected) or set(actual) != expected
                    or any(not math.isfinite(item.moderated_mark) or not 0 <= item.moderated_mark <= question["marks"]
                           for item in response.items)):
                raise ValueError("Invalid cached moderation")
            return [item.model_dump() for item in response.items]

        items = self.call("moderation", {"question": question, "entries": entries},
                          lambda: list(self.provider.moderate(question, entries).values()), validate)
        return {item["script_index"]: item for item in items}
