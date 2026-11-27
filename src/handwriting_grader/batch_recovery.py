"""Bounded question-batch recovery without overwriting human review.

Only explicitly typed Gemini safety blocks may be bisected. A singleton block
remains unscored; this module never changes provider safety settings.
"""

import math
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .grading import (
    GeminiSafetyBlocked, MAX_IMAGES_PER_REQUEST, VertexGrader,
    _validated_batch_grades, approved_inputs, attendance_guard, crop_image, grading_lock,
)
from .regions import atomic_json, file_hash
from .rubric import question_key
from .score_editor import load_state, writer_lock


def _policy_block(exc):
    return isinstance(exc, GeminiSafetyBlocked)


def inspect_batch_recovery(config, config_dir, config_path, question, *, locked=False):
    server = SimpleNamespace(config=config, config_dir=config_dir, config_path=config_path, run_id=None)
    root, revision, _, questions, _, review = load_state(server)
    if (root / "approval.json").exists():
        raise ValueError("Grading has already been approved")
    if not locked and (root / "processing.lock").exists():
        raise ValueError("Another grading process or a stale lock exists")
    from .grade_review import verify_grading_run
    _, manifest, results, _ = verify_grading_run(config, config_dir, config_path)
    if (root / "approval.json").exists():
        raise ValueError("Grading has already been approved")
    current_revision = ":".join(file_hash(root / name) for name in ("review.xlsx", "results.json", "manifest.json"))
    if current_revision != revision:
        raise ValueError("Grading or review changed while inspecting batch recovery")
    if manifest.get("grading_mode") != "batch":
        raise ValueError("Batch recovery requires a batch grading run")
    key = question_key(question)
    matches = [q for q in questions if question_key(q["question_number"]) == key]
    if len(matches) != 1:
        raise ValueError("Question is not in the approved rubric")
    rows = []
    for row in results["marks"]:
        pair = (row["script_index"], question_key(row["question"]))
        if pair[1] != key or not (row.get("grading_failed") or row.get("raw_mark") is None):
            continue
        item = review[pair]
        if (item["reviewed"] or item["manual_mark"] is not None
                or item["answer"] != row["answer"] or item["reasoning"] != row["reasoning"]):
            continue
        rows.append(row)
    return {
        "server": server, "root": root, "revision": revision, "manifest": manifest,
        "results": results, "question": matches[0], "rows": rows,
        "max_requests": 2 * len(rows) - math.ceil(len(rows) / MAX_IMAGES_PER_REQUEST) if rows else 0,
    }


def recover_question_batches(config, config_dir, config_path, question, revision, max_requests,
                             provider_factory=VertexGrader):
    if type(max_requests) is not int or max_requests < 1:
        raise ValueError("Request limit must be a positive integer")
    initial = inspect_batch_recovery(config, config_dir, config_path, question)
    root = initial["root"]
    with grading_lock(root):
        info = inspect_batch_recovery(config, config_dir, config_path, question, locked=True)
        if info["revision"] != revision:
            raise ValueError("Grading or review changed after preview")
        if not info["rows"]:
            raise ValueError("No unreviewed failed answers are eligible")
        _, _, _, boxes = approved_inputs(config, config_dir, config_path)
        guard = attendance_guard(config, config_dir, config_path, info["manifest"])
        key = question_key(info["question"]["question_number"])
        page, box = boxes[key]
        stride = config["layout"]["pages_per_student"]
        items = []
        for row in info["rows"]:
            name = f"images/{row['script_index'] * stride + page}.jpg"
            if row.get("page_image") != name:
                raise ValueError("Failed answer image path does not match the approved region")
            items.append({"script_index": row["script_index"], "image": crop_image(root / name, box)})

        history = root / "retry-history"
        history.mkdir(mode=0o700, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="bisect-", dir=history))
        for name, expected in zip(("review.xlsx", "results.json", "manifest.json"), revision.split(":")):
            shutil.copy2(root / name, backup / name)
            if file_hash(backup / name) != expected:
                raise ValueError("Grading or review changed while backing up")
        state = {"question": key, "revision": revision, "attempts": [], "grades": {},
                 "blocked": [], "pending": [item["script_index"] for item in items]}
        atomic_json(backup / "recovery.json", state)
        chunks = [items[i:i + MAX_IMAGES_PER_REQUEST] for i in range(0, len(items), MAX_IMAGES_PER_REQUEST)]
        pending = list(reversed(chunks))
        error = None
        manifest = info["manifest"]
        with provider_factory(manifest["models"], manifest["project"], manifest["location"]) as provider:
            while pending and len(state["attempts"]) < max_requests:
                guard()
                if (":".join(file_hash(root / name) for name in ("review.xlsx", "results.json", "manifest.json"))
                        != revision or (root / "approval.json").exists()):
                    raise ValueError(f"Review or grading changed; recovered grades remain in {backup}")
                batch = pending.pop()
                indices = [item["script_index"] for item in batch]
                try:
                    response = provider.grade_question_batch(info["question"], batch)
                    grades = _validated_batch_grades(response, indices, info["question"])
                    for grade in grades:
                        state["grades"][str(grade.script_index)] = grade.model_dump()
                    state["attempts"].append({"scripts": indices, "status": "succeeded"})
                except Exception as exc:
                    blocked = _policy_block(exc)
                    state["attempts"].append({
                        "scripts": indices, "status": "filtered" if blocked else "error",
                        "error_type": type(exc).__name__, "detail": str(exc)[:2000],
                        "request_id": getattr(exc, "request_id", None),
                    })
                    if blocked and len(batch) > 1:
                        middle = len(batch) // 2
                        pending.extend([batch[middle:], batch[:middle]])
                    elif blocked:
                        state["blocked"].extend(indices)
                    else:
                        pending.append(batch)
                        error = f"{type(exc).__name__}: {exc}"
                finally:
                    # The original pending list remains durable even on interruption.
                    state["pending"] = [item["script_index"] for group in reversed(pending) for item in group]
                    if not any(attempt["scripts"] == indices for attempt in state["attempts"]):
                        state["pending"] = indices + state["pending"]
                    atomic_json(backup / "recovery.json", state)
                if error:
                    break

        if state["grades"]:
            guard()
            with writer_lock(info["server"]):
                current = inspect_batch_recovery(config, config_dir, config_path, question, locked=True)
                if current["revision"] != revision:
                    raise ValueError(f"Review or grading changed; recovered grades remain in {backup}")
                eligible = {row["script_index"]: row for row in current["rows"]}
                for index, grade in state["grades"].items():
                    row = eligible[int(index)]
                    row.update(
                        answer=grade["answer"], raw_mark=grade["mark"], moderated_mark=grade["mark"],
                        similarity_score=grade["similarity_score"], reasoning=grade["reasoning"],
                        moderator_flag=grade["moderator_flag"], moderator_note=grade["moderator_note"],
                        grading_failed=False, batch_recovery_review_pending=True,
                    )
                    row.pop("grading_error_detail", None)
                    row.pop("image_policy_block", None)
                atomic_json(root / "results.json", current["results"])
        return {"recovered": len(state["grades"]), "blocked": len(state["blocked"]),
                "pending": len(state["pending"]), "requests": len(state["attempts"]),
                "error": error, "history": backup}
