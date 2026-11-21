"""Generate optional Vertex AI region proposals without modifying drafts."""

import json
import hashlib
import os
import tempfile
import time
from pathlib import Path

import yaml
from google import genai
from google.genai import types
from pydantic import BaseModel, StrictInt, StrictStr

from .regions import file_hash, validate_annotations, verify_run
from .cache import CachedGrader, cache_directory, enabled_agents
from .telemetry import get_active_tracker


class SuggestedBox(BaseModel):
    x: StrictInt
    y: StrictInt
    width: StrictInt
    height: StrictInt
    label: StrictStr


class SuggestedBoxes(BaseModel):
    boxes: list[SuggestedBox]


PROMPT = """Identify answer and metadata regions on this scanned exam page.

For metadata fields (NAME, ID, CLASS):
- The bounding box MUST enclose the handwritten/input value area (the blank line, box, or write-in space where the student writes), NOT the printed label text (such as "Name:", "Student ID:", or "Class:").
- For NAME: Allocate generous horizontal width across the entire designated answer line/blank to accommodate variable student name lengths.
- For ID and CLASS: Enclose the complete write-in area or box designated for the student's ID and class.
- Use exact labels "NAME", "ID", and "CLASS".

For question answer areas:
- Expected question IDs from the marking rubric: {question_ids}.
{question_details}- Return ONE bounding box enclosing the complete student answer area/table for each expected question ID that appears on this page.
- Use ONLY the exact question ID labels listed above. Do NOT invent, rename, or subdivide labels beyond that list.
- Do not include printed question instructions or titles in the answer box.

Coordinates must use a normalized 0-to-1000 scale: x and y are the top-left corner, and width and height are the box dimensions. Ensure all boxes lie entirely inside the page. Return an empty list if no regions can be identified."""


def load_model_settings(path, agent="annotation"):
    path = Path(path)
    with path.open(encoding="utf-8") as file:
        settings = yaml.safe_load(file)
    if not isinstance(settings, dict):
        raise ValueError("Model configuration must be a mapping")
    vertex = settings.get("vertex_ai")
    models = settings.get("models")
    if not isinstance(vertex, dict) or vertex.get("use_vertexai") is not True:
        raise ValueError("Model configuration must enable Vertex AI")
    model = models.get(agent) if isinstance(models, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"Model configuration needs models.{agent}")
    return model.strip()


def create_json_once(path, data):
    """Atomically publish a new suggestions file without replacing an old one."""
    path = Path(path)
    if path.exists():
        raise ValueError("AI suggestions already exist; do not overwrite them")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            raise ValueError("AI suggestions already exist; do not overwrite them")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def build_prompt(question_ids=None, question_summaries=None):
    id_list = ", ".join(question_ids) if question_ids else "none"
    details = ""
    if question_summaries:
        lines = [f"  - {qid}: {summary}" for qid, summary in question_summaries]
        details = "- Question reference from rubric:\n" + "\n".join(lines) + "\n"
    return PROMPT.format(question_ids=id_list, question_details=details)


def load_rubric_questions(config, config_dir):
    from .rubric import read_rubric, rubric_root
    rubric_path = rubric_root(config, config_dir) / "rubric.xlsx"
    if not rubric_path.exists():
        return [], []
    questions = read_rubric(rubric_path)
    ids = []
    summaries = []
    for q in questions:
        val = str(q.get("question_number") or q.get("question") or "").strip()
        if val:
            ids.append(val)
            text = " ".join(str(q.get("question_text") or "").split())
            if len(text) > 100:
                text = text[:97] + "..."
            summaries.append((val, text))
    return ids, summaries


def generate_suggestions(config, config_dir, config_path, model_config_path, project, location):
    """Send layout images to Vertex; caller must already have obtained consent."""
    model_path = Path(model_config_path).expanduser().resolve()
    model_config_digest = file_hash(model_path)
    model = load_model_settings(model_path)
    if file_hash(model_path) != model_config_digest:
        raise ValueError("Model configuration changed while loading")
    root, manifest = verify_run(config, config_dir, config_path)
    output = root / "ai_suggestions.json"
    if output.exists():
        raise ValueError("AI suggestions already exist; do not overwrite them")
    question_ids, question_summaries = load_rubric_questions(config, config_dir)
    if not question_ids:
        raise ValueError(
            "Cannot suggest regions without a marking rubric question list. "
            "Extract or import the rubric first (extract-rubric / import-rubric)."
        )
    prompt_text = build_prompt(question_ids, question_summaries)
    suggestions = {}
    agents = [name for name in enabled_agents(model_path) if name == "annotation"]
    cache = CachedGrader(None, cache_directory(model_path, root) if agents else root / "cache", agents,
                         {"annotation": model}, project, location, source_files=["suggestions.py", "regions.py"])
    options = types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1))
    with genai.Client(vertexai=True, project=project, location=location, http_options=options) as client:
        for index, page in enumerate(manifest["pages"]):
            image = (root / "images" / f"{index}.jpg").read_bytes()
            def compute():
                t0 = time.monotonic()
                response = client.models.generate_content(
                    model=model,
                    contents=[prompt_text, types.Part.from_bytes(data=image, mime_type="image/jpeg")],
                    config=types.GenerateContentConfig(
                        temperature=0, max_output_tokens=8192,
                        response_mime_type="application/json", response_schema=SuggestedBoxes,
                    ),
                )
                tracker = get_active_tracker()
                if tracker is not None:
                    tracker.record_call(
                        phase="suggest_regions",
                        step_label=f"Template page {index + 1}",
                        model=model,
                        usage_metadata=getattr(response, "usage_metadata", None),
                        duration_sec=time.monotonic() - t0,
                    )
                return (SuggestedBoxes.model_validate(response.parsed) if response.parsed is not None
                        else SuggestedBoxes.model_validate_json(response.text)).model_dump()

            def validate(value):
                parsed = SuggestedBoxes.model_validate(value)
                for box in parsed.boxes:
                    if (box.x < 0 or box.y < 0 or box.width <= 0 or box.height <= 0
                            or box.x + box.width > 1000 or box.y + box.height > 1000):
                        raise ValueError(f"Vertex returned an invalid normalized box on page {index + 1}")
                return parsed.model_dump()

            parsed = SuggestedBoxes.model_validate(cache.call(
                "annotation",
                {"image_sha256": hashlib.sha256(image).hexdigest(), "question_ids": question_ids},
                compute,
                validate,
            ))
            boxes = []
            for box in parsed.boxes:
                left = round(box.x * page["width"] / 1000)
                top = round(box.y * page["height"] / 1000)
                right = round((box.x + box.width) * page["width"] / 1000)
                bottom = round((box.y + box.height) * page["height"] / 1000)
                raw_label = box.label.strip()
                normalized = raw_label.upper()
                if normalized in {"NAME", "STUDENT NAME"}:
                    if index != 0:
                        continue
                    label = "NAME"
                elif normalized in {"ID", "STUDENT ID"}:
                    if index != 0:
                        continue
                    label = "ID"
                elif normalized in {"CLASS", "STUDENT CLASS"}:
                    if index != 0:
                        continue
                    label = "CLASS"
                else:
                    label = raw_label
                boxes.append({
                    "x": left, "y": top, "width": right - left, "height": bottom - top,
                    "label": label,
                })
            suggestions[str(index)] = boxes
    validate_annotations(suggestions, manifest["pages"])
    verify_run(config, config_dir, config_path)
    if file_hash(model_path) != model_config_digest:
        raise ValueError("Model configuration changed while generating suggestions")
    create_json_once(output, {
        "version": 1,
        "config_sha256": manifest["config_sha256"],
        "pdf_sha256": manifest["pdf_sha256"],
        "model_config_sha256": model_config_digest,
        "project": project,
        "location": location,
        "model": model,
        "annotations": suggestions,
    })
    return output
