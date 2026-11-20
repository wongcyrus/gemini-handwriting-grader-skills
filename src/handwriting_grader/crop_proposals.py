"""Reviewable shared and per-script question crop proposals."""

import json
import os
import shutil
from pathlib import Path

from .grading import approved_inputs
from .regions import atomic_json, config_hash, file_hash, resolve_path
from .rubric import question_key
from .run_context import run_directory


def read_crop_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Crop JSON cannot contain duplicate keys")
            result[key] = value
        return result

    with path.open(encoding="utf-8") as file:
        return json.load(file, object_pairs_hook=unique_keys)


def proposal_directory(config, config_dir, proposal_id):
    run_directory(config, config_dir, proposal_id)
    if proposal_id is None:
        raise ValueError("A crop proposal ID is required")
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    proposals = output / "regrade-crops"
    target = proposals / proposal_id
    if proposals.resolve().parent != output or target.resolve().parent != proposals.resolve():
        raise ValueError("Crop proposal directory cannot escape the exam output")
    return target


def resolve_proposed_crops(config, config_dir, config_path, parent, question, proposal):
    key = question_key(question)
    _, _, region_manifest, boxes = approved_inputs(config, config_dir, config_path)
    if key not in {question_key(q["question_number"]) for q in parent[3]}:
        raise ValueError("Question is not in the parent rubric")
    page, original = boxes[key]
    dimensions = region_manifest["pages"][page]
    if not isinstance(proposal, dict) or set(proposal) - {"shared", "students"}:
        raise ValueError("Crop proposal must contain only shared and students")
    shared = proposal.get("shared")
    students = proposal.get("students", {})
    if not isinstance(students, dict) or (shared is None and not students):
        raise ValueError("Provide a shared crop or at least one student crop")

    def validate(box):
        if not isinstance(box, dict) or set(box) != {"x", "y", "width", "height"}:
            raise ValueError("Each crop must contain x, y, width and height")
        if any(type(value) is not int for value in box.values()):
            raise ValueError("Crop coordinates must be whole numbers")
        if (box["x"] < 0 or box["y"] < 0 or box["width"] <= 0 or box["height"] <= 0
                or box["x"] + box["width"] > dimensions["width"]
                or box["y"] + box["height"] > dimensions["height"]):
            raise ValueError("Crop must fit within the approved question page")
        return dict(box)

    count = len(parent[5])
    inherited = parent[1].get("effective_crops", {}).get(key)
    base = [{field: original[field] for field in ("x", "y", "width", "height")}
            for _ in range(count)] if inherited is None else [dict(box) for box in inherited]
    if len(base) != count:
        raise ValueError("Parent crop count does not match the roster")
    if shared is not None:
        box = validate(shared)
        base = [dict(box) for _ in range(count)]
    for index, box in students.items():
        if not isinstance(index, str) or not index.isascii() or not index.isdigit() or str(int(index)) != index:
            raise ValueError("Student crop keys must be zero-based script indices")
        if int(index) >= count:
            raise ValueError("Student crop index is outside the present roster")
        base[int(index)] = validate(box)
    return [validate(box) for box in base]


def prepare_crop_proposal(config, config_dir, config_path, proposal_id, parent_run, question, source):
    from .regrading import parent_snapshot

    parent, provenance = parent_snapshot(config, config_dir, config_path, parent_run)
    source = Path(source).expanduser().resolve()
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    if source == output or output in source.parents or source.suffix.lower() != ".json":
        raise ValueError("The crop JSON must be outside the output directory")
    source_hash = file_hash(source)
    proposal = read_crop_json(source)
    key = question_key(question)
    crops = resolve_proposed_crops(config, config_dir, config_path, parent, key, proposal)
    if file_hash(source) != source_hash or parent_snapshot(config, config_dir, config_path, parent_run)[1] != provenance:
        raise ValueError("Parent or crop proposal changed during preparation")
    target = proposal_directory(config, config_dir, proposal_id)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Crop proposal already exists; preserve it") from exc
    with source.open("rb") as src, (target / "crops.json").open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if file_hash(target / "crops.json") != source_hash or file_hash(source) != source_hash:
        raise ValueError("Crop proposal changed while copying")
    atomic_json(target / "manifest.json", {
        "version": 1, "exam_config_sha256": config_hash(config), **provenance,
        "question_key": key, "source_path": str(source), "source_sha256": source_hash,
        "crops_sha256": source_hash, "resolved_crops": crops,
    })
    return target


def validate_crop_proposal(config, config_dir, config_path, proposal_id, *, require_approval=True):
    from .regrading import parent_snapshot

    target = proposal_directory(config, config_dir, proposal_id)
    with (target / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Invalid crop proposal")
    parent, provenance = parent_snapshot(config, config_dir, config_path, manifest.get("parent_run"))
    if (manifest.get("exam_config_sha256") != config_hash(config)
            or any(manifest.get(key) != value for key, value in provenance.items())
            or manifest.get("source_sha256") != file_hash(manifest["source_path"])
            or manifest.get("crops_sha256") != file_hash(target / "crops.json")):
        raise ValueError("Crop proposal or parent inputs changed")
    crops = resolve_proposed_crops(config, config_dir, config_path, parent, manifest["question_key"],
                                   read_crop_json(target / "crops.json"))
    if crops != manifest.get("resolved_crops"):
        raise ValueError("Resolved crops changed")
    if require_approval:
        with (target / "approval.json").open(encoding="utf-8") as file:
            approval = json.load(file)
        if (approval.get("version") != 1
                or approval.get("manifest_sha256") != file_hash(target / "manifest.json")
                or approval.get("crops_sha256") != file_hash(target / "crops.json")):
            raise ValueError("Crop proposal has not been approved or has changed")
    return target, manifest, crops


def approve_crop_proposal(config, config_dir, config_path, proposal_id):
    target, _, _ = validate_crop_proposal(config, config_dir, config_path, proposal_id, require_approval=False)
    approval = {"version": 1, "manifest_sha256": file_hash(target / "manifest.json"),
                "crops_sha256": file_hash(target / "crops.json")}
    try:
        with (target / "approval.json").open("x", encoding="utf-8") as file:
            json.dump(approval, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError as exc:
        raise ValueError("This crop proposal was already approved") from exc
    validate_crop_proposal(config, config_dir, config_path, proposal_id)
    return target / "approval.json"
