"""Versioned, human-reviewed reruns of one question for every present student."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from .grading import (VertexGrader, approved_inputs, continue_grading, grading_lock,
                      grading_models, safe_append, write_review)
from .regions import atomic_json, config_hash, file_hash, resolve_path
from .rubric import question_key, read_rubric
from .run_context import run_directory


def compare_rubrics(parent, candidate, selected):
    old = {question_key(q["question_number"]): q for q in parent}
    new = {question_key(q["question_number"]): q for q in candidate}
    if (selected not in old or set(old) != set(new)
            or list(old) != list(new)):
        raise ValueError("The selected question and all original questions must be present")
    for key in old:
        if old[key]["question_number"] != new[key]["question_number"]:
            raise ValueError("Question numbers cannot be changed")
        if key != selected and old[key] != new[key]:
            raise ValueError("Only the selected question's rubric can change")
    return new[selected]


def proposal_directory(config, config_dir, proposal_id):
    run_directory(config, config_dir, proposal_id)
    if proposal_id is None:
        raise ValueError("A proposal ID is required")
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    proposals = output / "regrade-rubrics"
    target = proposals / proposal_id
    if proposals.resolve().parent != output or target.resolve().parent != proposals.resolve():
        raise ValueError("Rubric proposal directory cannot escape the exam output")
    return target


def parent_snapshot(config, config_dir, config_path, parent_run):
    from .grade_review import read_approved_grading

    parent = read_approved_grading(config, config_dir, config_path, run_id=parent_run)
    root = parent[0]
    return parent, {
        "parent_run": parent_run, "parent_approval_sha256": file_hash(root / "approval.json"),
        "parent_review_sha256": file_hash(root / "review.xlsx"),
        "parent_results_sha256": file_hash(root / "results.json"),
        "parent_manifest_sha256": file_hash(root / "manifest.json"),
    }


def prepare_rubric_proposal(config, config_dir, config_path, proposal_id, parent_run, question, source):
    parent, provenance = parent_snapshot(config, config_dir, config_path, parent_run)
    key = question_key(question)
    source = Path(source).expanduser().resolve()
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    if source == output or output in source.parents:
        raise ValueError("The replacement rubric must be outside the output directory")
    if source.suffix.lower() != ".xlsx":
        raise ValueError("The replacement rubric must be an XLSX workbook")
    source_hash = file_hash(source)
    candidate = read_rubric(source)
    compare_rubrics(parent[3], candidate, key)
    from .reporting import question_passing_policy

    question_passing_policy(config, candidate)
    if file_hash(source) != source_hash or parent_snapshot(config, config_dir, config_path, parent_run)[1] != provenance:
        raise ValueError("Parent or rubric changed during proposal preparation")
    target = proposal_directory(config, config_dir, proposal_id)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Rubric proposal already exists; preserve it") from exc
    with source.open("rb") as src, (target / "rubric.xlsx").open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if file_hash(target / "rubric.xlsx") != source_hash or file_hash(source) != source_hash:
        raise ValueError("Replacement rubric changed while copying")
    atomic_json(target / "manifest.json", {
        "version": 1, "exam_config_sha256": config_hash(config), **provenance,
        "question_key": key, "source_path": str(source), "source_sha256": source_hash,
        "rubric_sha256": file_hash(target / "rubric.xlsx"),
    })
    return target


def validate_proposal(config, config_dir, config_path, proposal_id, *, require_approval=True):
    target = proposal_directory(config, config_dir, proposal_id)
    with (target / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Invalid replacement rubric proposal")
    parent, provenance = parent_snapshot(config, config_dir, config_path, manifest.get("parent_run"))
    if (manifest.get("exam_config_sha256") != config_hash(config)
            or any(manifest.get(key) != value for key, value in provenance.items())
            or manifest.get("source_sha256") != file_hash(manifest["source_path"])
            or manifest.get("rubric_sha256") != file_hash(target / "rubric.xlsx")):
        raise ValueError("Rubric proposal or parent inputs changed")
    candidate = read_rubric(target / "rubric.xlsx")
    compare_rubrics(parent[3], candidate, manifest["question_key"])
    if require_approval:
        with (target / "approval.json").open(encoding="utf-8") as file:
            approval = json.load(file)
        if (approval.get("version") != 1
                or approval.get("manifest_sha256") != file_hash(target / "manifest.json")
                or approval.get("rubric_sha256") != file_hash(target / "rubric.xlsx")):
            raise ValueError("Replacement rubric has not been approved or has changed")
    return target, manifest, candidate


def approve_rubric_proposal(config, config_dir, config_path, proposal_id):
    target, _, _ = validate_proposal(config, config_dir, config_path, proposal_id, require_approval=False)
    approval = {"version": 1, "manifest_sha256": file_hash(target / "manifest.json"),
                "rubric_sha256": file_hash(target / "rubric.xlsx")}
    try:
        with (target / "approval.json").open("x", encoding="utf-8") as file:
            json.dump(approval, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError as exc:
        raise ValueError("This rubric proposal was already approved") from exc
    validate_proposal(config, config_dir, config_path, proposal_id)
    return target / "approval.json"


def private_parent_cache(path):
    root = Path(path)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError("The parent cache directory is unavailable or unsafe")
    info = root.stat()
    if hasattr(os, "getuid") and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("Parent cache directory must be owned by the user and private (mode 0700)")
    return root


def inspect_regrade(config, config_dir, config_path, run_id, parent_run, question,
                    model_config_path, project, location, proposal_id=None, crop_proposal_id=None,
                    reuse_parent_cache=False):
    from .cache import cache_directory, enabled_agents

    if run_id is None:
        raise ValueError("A new run ID is required")
    target = run_directory(config, config_dir, run_id)
    if run_id == parent_run:
        raise ValueError("The child and parent run IDs must differ")
    if target.exists():
        raise ValueError("The selected child run already exists; preserve it")
    if (not project.strip() or not location.strip()
            or any(char.isspace() for char in project) or any(char.isspace() for char in location)):
        raise ValueError("Vertex project and location must be provided")
    parent, provenance = parent_snapshot(config, config_dir, config_path, parent_run)
    key = question_key(question)
    candidate = parent[3]
    rubric = (parent[0].parent / "rubric.xlsx" if parent_run is not None
              else resolve_path(config_dir, config["exam"]["output_dir"]) / "rubric" / "rubric.xlsx")
    proposal_approval = None
    if proposal_id is not None:
        proposal, proposal_manifest, candidate = validate_proposal(config, config_dir, config_path, proposal_id)
        if (proposal_manifest["parent_run"] != parent_run or proposal_manifest["question_key"] != key):
            raise ValueError("Rubric proposal does not match the selected parent and question")
        rubric = proposal / "rubric.xlsx"
        proposal_approval = file_hash(proposal / "approval.json")
    compare_rubrics(parent[3], candidate, key)
    from .reporting import question_passing_policy

    question_passing_policy(config, candidate)
    effective_crops = dict(parent[1].get("effective_crops", {}))
    crop_approval = None
    if crop_proposal_id is not None:
        from .crop_proposals import validate_crop_proposal

        crop_root, crop_manifest, crops = validate_crop_proposal(config, config_dir, config_path, crop_proposal_id)
        if crop_manifest["parent_run"] != parent_run or crop_manifest["question_key"] != key:
            raise ValueError("Crop proposal does not match the selected parent and question")
        effective_crops[key] = crops
        crop_approval = file_hash(crop_root / "approval.json")
    models, model_hash = grading_models(Path(model_config_path))
    cache_agents = [agent for agent in enabled_agents(Path(model_config_path))
                    if agent in {"ocr", "grading", "moderation"}]
    cache_root = cache_directory(Path(model_config_path), target / "grading") if cache_agents else None
    if reuse_parent_cache:
        parent_cache = parent[1].get("cache_root")
        parent_agents = parent[1].get("cache_agents")
        if (not cache_agents or not isinstance(parent_cache, str) or not isinstance(parent_agents, list)
                or not set(cache_agents).intersection(parent_agents)):
            raise ValueError("Parent cache reuse requires enabled compatible parent and child cache agents")
        cache_root = private_parent_cache(parent_cache)
    if file_hash(model_config_path) != model_hash:
        raise ValueError("Model configuration changed during preview")
    return {
        "target": target, "parent": parent, "provenance": provenance, "question_key": key,
        "questions": candidate, "rubric": rubric, "rubric_sha256": file_hash(rubric),
        "proposal_id": proposal_id, "proposal_approval_sha256": proposal_approval,
        "crop_proposal_id": crop_proposal_id, "crop_approval_sha256": crop_approval,
        "effective_crops": effective_crops,
        "models": models, "model_config_sha256": model_hash,
        "cache_agents": cache_agents, "cache_root": cache_root,
        "reuse_parent_cache": reuse_parent_cache,
    }


def validate_child_lineage(config, config_dir, config_path, run_id, manifest, seen):
    from .grade_review import read_approved_grading

    parent_run = manifest.get("parent_run")
    if manifest.get("run_id") != run_id:
        raise ValueError("Child run ID does not match its manifest")
    if parent_run == run_id:
        raise ValueError("A child run cannot be its own parent")
    parent = read_approved_grading(config, config_dir, config_path, run_id=parent_run, _seen=seen)
    root = parent[0]
    hashes = {
        "parent_approval_sha256": file_hash(root / "approval.json"),
        "parent_review_sha256": file_hash(root / "review.xlsx"),
        "parent_results_sha256": file_hash(root / "results.json"),
        "parent_manifest_sha256": file_hash(root / "manifest.json"),
    }
    if any(manifest.get(key) != value for key, value in hashes.items()):
        raise ValueError("The parent run changed since regrading")
    reuse_cache = manifest.get("reuse_parent_cache", False)
    if type(reuse_cache) is not bool:
        raise ValueError("Invalid parent cache reuse setting")
    if reuse_cache and (
        manifest.get("cache_root") != parent[1].get("cache_root")
        or not isinstance(manifest.get("cache_agents"), list)
        or not isinstance(parent[1].get("cache_agents"), list)
        or not set(manifest["cache_agents"]).intersection(parent[1]["cache_agents"])
    ):
        raise ValueError("The parent cache reuse settings changed")
    rubric = run_directory(config, config_dir, run_id) / "rubric.xlsx"
    if manifest.get("rubric_sha256") != file_hash(rubric):
        raise ValueError("The child rubric changed")
    candidate = read_rubric(rubric)
    compare_rubrics(parent[3], candidate, manifest.get("question_key"))
    if manifest.get("image_sha256") != parent[1].get("image_sha256"):
        raise ValueError("Child images do not match the approved parent")
    proposal_id = manifest.get("proposal_id")
    if proposal_id is not None:
        proposal, proposal_manifest, _ = validate_proposal(config, config_dir, config_path, proposal_id)
        if (proposal_manifest["parent_run"] != parent_run
                or proposal_manifest["question_key"] != manifest.get("question_key")
                or file_hash(proposal / "approval.json") != manifest.get("proposal_approval_sha256")
                or file_hash(proposal / "rubric.xlsx") != file_hash(rubric)):
            raise ValueError("The approved replacement rubric changed")
    elif manifest.get("proposal_approval_sha256") is not None or candidate != parent[3]:
        raise ValueError("A changed rubric requires a separately approved proposal")
    expected_crops = dict(parent[1].get("effective_crops", {}))
    crop_id = manifest.get("crop_proposal_id")
    if crop_id is not None:
        from .crop_proposals import validate_crop_proposal

        crop_root, crop_manifest, crops = validate_crop_proposal(config, config_dir, config_path, crop_id)
        if (crop_manifest["parent_run"] != parent_run
                or crop_manifest["question_key"] != manifest.get("question_key")
                or file_hash(crop_root / "approval.json") != manifest.get("crop_approval_sha256")):
            raise ValueError("The approved crop proposal changed")
        expected_crops[manifest["question_key"]] = crops
    elif manifest.get("crop_approval_sha256") is not None:
        raise ValueError("Changed crops require a separately approved proposal")
    if manifest.get("effective_crops", {}) != expected_crops:
        raise ValueError("The child crops do not match approved lineage")
    return candidate, rubric, parent


def validate_child_checkpoint(manifest, results, parent):
    with (parent[0] / "results.json").open(encoding="utf-8") as file:
        parent_results = json.load(file)
    if results.get("checkpoint_version") != 1 or results.get("scripts") != parent_results["scripts"]:
        raise ValueError("Child checkpoint identities do not match the parent")
    selected = manifest["question_key"]
    expected = {(item["script_index"], question_key(item["question"])): item
                for item in parent_results["marks"] if question_key(item["question"]) != selected}
    marks = results.get("marks")
    if not isinstance(marks, list):
        raise ValueError("Child checkpoint marks are invalid")
    inherited = {}
    selected_indices = set()
    for item in marks:
        if not isinstance(item, dict):
            raise ValueError("Child checkpoint contains an invalid mark")
        pair = (item.get("script_index"), question_key(item.get("question", "")))
        if type(pair[0]) is not int:
            raise ValueError("Child checkpoint contains an invalid script")
        if pair[1] == selected:
            if pair[0] not in parent[5] or pair[0] in selected_indices:
                raise ValueError("Child checkpoint contains a duplicate or unknown rerun answer")
            selected_indices.add(pair[0])
        elif pair in inherited or pair not in expected or item != expected[pair]:
            raise ValueError("Inherited child results do not match the parent")
        else:
            inherited[pair] = item
    if set(inherited) != set(expected):
        raise ValueError("Inherited child results are incomplete")
    moderated = results.get("moderated_questions")
    unchanged = {key for _, key in expected}
    if (not isinstance(moderated, list) or any(not isinstance(key, str) for key in moderated)
            or len(moderated) != len(set(moderated))
            or set(moderated) not in (unchanged, unchanged | {selected})
            or (selected in moderated and selected_indices != set(parent[5]))):
        raise ValueError("Child moderation checkpoint is invalid")


def create_review_draft(path, results, parent, selected):
    write_review(path, results)
    workbook = load_workbook(path)
    try:
        for row in workbook["Scripts"].iter_rows(min_row=2):
            row[3].value = parent[5][row[0].value]
            row[3].data_type = "s"
            row[4].value = None
        previous = {(item["script_index"], question_key(item["question"])): item for item in parent[2]["marks"]}
        for row in workbook["Marks"].iter_rows(min_row=2):
            pair = (row[0].value, question_key(row[1].value))
            if pair[1] != selected:
                row[3].value = previous[pair]["answer"]
                row[3].data_type = "s"
                row[6].value = previous[pair]["reasoning"]
                row[6].data_type = "s"
                row[10].value = parent[6][pair]
            row[11].value = None
        comparison = workbook.create_sheet("Previous")
        comparison.append(["script_index", "question", "previous_answer", "previous_reasoning",
                           "previous_mark", "rerun"])
        for pair, item in previous.items():
            safe_append(comparison, [pair[0], item["question"], item["answer"], item["reasoning"],
                                     parent[6][pair], "yes" if pair[1] == selected else "no"])
        workbook.save(path)
    finally:
        workbook.close()


def finish_child(config, config_dir, config_path, run_id, target, manifest, results):
    from .grade_review import verify_grading_run, read_approved_grading

    _, _, _, questions = verify_grading_run(config, config_dir, config_path, run_id=run_id,
                                             require_completed=False)
    parent = read_approved_grading(config, config_dir, config_path, run_id=manifest["parent_run"])
    indices = set(parent[5])
    keys = {question_key(q["question_number"]) for q in questions}
    scripts = [item["script_index"] for item in results["scripts"]]
    marks = [(item["script_index"], question_key(item["question"])) for item in results["marks"]]
    if (len(scripts) != len(indices) or set(scripts) != indices
            or len(marks) != len(indices) * len(keys)
            or set(marks) != {(index, key) for index in indices for key in keys}
            or set(results.get("moderated_questions", [])) != keys):
        raise ValueError("Child grading checkpoint is incomplete")
    with tempfile.TemporaryDirectory(prefix=".review-", dir=target) as temp:
        draft = Path(temp) / "review.xlsx"
        create_review_draft(draft, results, parent, manifest["question_key"])
        verify_grading_run(config, config_dir, config_path, run_id=run_id, require_completed=False)
        if (target / "review.xlsx").exists() or (target / "approval.json").exists():
            raise ValueError("A child review or approval already exists")
        results["completed"] = True
        atomic_json(target / "results.json", results)
        os.link(draft, target / "review.xlsx")


def continue_child(config, config_dir, config_path, run_id, target, manifest, results,
                   provider_factory=VertexGrader):
    from .grade_review import verify_grading_run

    _, _, _, questions = verify_grading_run(config, config_dir, config_path, run_id=run_id,
                                             require_completed=False)
    _, _, _, boxes = approved_inputs(config, config_dir, config_path)
    parent, _ = parent_snapshot(config, config_dir, config_path, manifest["parent_run"])

    def guard():
        verify_grading_run(config, config_dir, config_path, run_id=run_id, require_completed=False)

    if not results.get("completed"):
        if manifest.get("reuse_parent_cache"):
            private_parent_cache(manifest["cache_root"])
        continue_grading(target, results, questions, boxes, {"students": len(parent[5])},
                         config["layout"]["pages_per_student"], manifest["models"], manifest["project"],
                         manifest["location"], provider_factory, manifest["cache_agents"],
                         Path(manifest["cache_root"]), manifest["metadata_fields"], guard,
                         effective_crops=manifest.get("effective_crops", {}))
    finish_child(config, config_dir, config_path, run_id, target, manifest, results)


def regrade(config, config_dir, config_path, run_id, parent_run, question, model_config_path,
            project, location, proposal_id=None, crop_proposal_id=None, reuse_parent_cache=False,
            *, provider_factory=VertexGrader):
    info = inspect_regrade(config, config_dir, config_path, run_id, parent_run, question,
                           model_config_path, project, location, proposal_id, crop_proposal_id, reuse_parent_cache)
    parent = info["parent"]
    target_run = info["target"]
    target_run.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        target_run.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("The selected child run already exists") from exc
    target = target_run / "grading"
    target.mkdir(mode=0o700)
    image_dir = target / "images"
    image_dir.mkdir(mode=0o700)
    shutil.copyfile(info["rubric"], target_run / "rubric.xlsx")
    if file_hash(target_run / "rubric.xlsx") != info["rubric_sha256"]:
        raise ValueError("Rubric changed during child creation")
    for index, digest in enumerate(parent[1]["image_sha256"]):
        image = image_dir / f"{index}.jpg"
        shutil.copyfile(parent[0] / "images" / f"{index}.jpg", image)
        if file_hash(image) != digest:
            raise ValueError("A parent image changed during child creation")
    cache_agents = info["cache_agents"]
    cache_root = info["cache_root"] if cache_agents else target / "cache"
    manifest = {
        **parent[1], **info["provenance"], "version": 2, "run_id": run_id, "question_key": info["question_key"],
        "rubric_sha256": info["rubric_sha256"], "proposal_id": proposal_id,
        "proposal_approval_sha256": info["proposal_approval_sha256"],
        "crop_proposal_id": crop_proposal_id, "crop_approval_sha256": info["crop_approval_sha256"],
        "effective_crops": info["effective_crops"],
        "reuse_parent_cache": info["reuse_parent_cache"],
        "model_config_path": str(Path(model_config_path).resolve()), "model_config_sha256": info["model_config_sha256"],
        "project": project, "location": location, "models": info["models"], "cache_agents": cache_agents,
        "cache_root": str(cache_root),
        "metadata_fields": parent[1].get("metadata_fields", ["ID", "CLASS"]),
    }
    atomic_json(target / "manifest.json", manifest)
    with (parent[0] / "results.json").open(encoding="utf-8") as file:
        raw = json.load(file)
    selected = info["question_key"]
    results = {"version": 1, "checkpoint_version": 1, "completed": False,
               "scripts": raw["scripts"],
               "marks": [item for item in raw["marks"] if question_key(item["question"]) != selected],
               "moderated_questions": [question_key(q["question_number"]) for q in info["questions"]
                                       if question_key(q["question_number"]) != selected]}
    atomic_json(target / "results.json", results)
    with grading_lock(target):
        continue_child(config, config_dir, config_path, run_id, target, manifest, results, provider_factory)
    return target_run


def resume_regrade(config, config_dir, config_path, run_id, *, provider_factory=VertexGrader):
    from .grade_review import verify_grading_run

    if run_id is None:
        raise ValueError("A child run ID is required")
    target = run_directory(config, config_dir, run_id) / "grading"
    with grading_lock(target):
        _, manifest, results, _ = verify_grading_run(config, config_dir, config_path,
                                                       run_id=run_id, require_completed=False)
        if results.get("checkpoint_version") != 1:
            raise ValueError("Unsupported child checkpoint")
        if (target / "review.xlsx").exists() or (target / "approval.json").exists():
            raise ValueError("A review or approval already exists; do not overwrite it")
        continue_child(config, config_dir, config_path, run_id, target, manifest, results, provider_factory)
    return target
