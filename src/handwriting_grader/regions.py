"""Prepare and validate page-layout annotation drafts."""

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
from PIL import Image


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_hash(config):
    return hashlib.sha256(yaml.safe_dump(config, sort_keys=True).encode()).hexdigest()


def resolve_path(config_dir, value):
    return (config_dir / Path(value).expanduser()).resolve()


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        temp = Path(file.name)
        try:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def prepare_regions(config, config_dir, page_count):
    """Render the first script's pages, without replacing an existing run."""
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise ValueError("pdftoppm is required; install Poppler before preparing regions")
    pdf = resolve_path(config_dir, config["exam"]["scanned_pdf"])
    output = resolve_path(config_dir, config["exam"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    target = output / "regions"
    if target.exists():
        raise ValueError("regions already exists; choose a different output directory")
    source_hash = file_hash(pdf)
    with tempfile.TemporaryDirectory(prefix=".regions-", dir=output) as temp:
        temporary = Path(temp)
        prefix = temporary / "page"
        result = subprocess.run(
            [renderer, "-f", "1", "-l", str(page_count), "-r", "200",
             "-jpeg", str(pdf), str(prefix)],
            capture_output=True, timeout=300, check=False,
        )
        if result.returncode:
            raise ValueError("PDF page rendering failed")
        rendered = sorted(
            temporary.glob("page-*.jpg"),
            key=lambda path: int(path.stem.removeprefix("page-")),
        )
        if len(rendered) != page_count:
            raise ValueError("PDF renderer returned an unexpected number of pages")
        if file_hash(pdf) != source_hash:
            raise ValueError("The PDF changed during rendering; start again")
        staging = temporary / "result"
        images = staging / "images"
        images.mkdir(parents=True, mode=0o700)
        dimensions = []
        for index, image in enumerate(rendered):
            with Image.open(image) as data:
                dimensions.append({
                    "width": data.width, "height": data.height, "sha256": file_hash(image)
                })
            image.rename(images / f"{index}.jpg")
        manifest = {
            "version": 1,
            "config_sha256": config_hash(config),
            "pdf_sha256": source_hash,
            "pages": dimensions,
        }
        atomic_json(staging / "manifest.json", manifest)
        atomic_json(staging / "draft.json", {
            "revision": 0,
            "annotations": {str(i): [] for i in range(page_count)},
            "reviewed": [],
        })
        # mkdir provides exclusive reservation without replacing another run.
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ValueError("regions already exists; choose a different output directory") from exc
        try:
            for item in staging.iterdir():
                item.rename(target / item.name)
        except OSError as exc:
            raise ValueError(f"Region preparation was interrupted; inspect {target}") from exc
    return target


def verify_run(config, config_dir, config_path=None):
    if config_path is not None:
        with Path(config_path).open(encoding="utf-8") as file:
            try:
                current_config = yaml.safe_load(file)
            except yaml.YAMLError as exc:
                raise ValueError("Exam configuration is invalid") from exc
        if not isinstance(current_config, dict) or config_hash(current_config) != config_hash(config):
            raise ValueError("Exam configuration changed; prepare a new region run")
    root = resolve_path(config_dir, config["exam"]["output_dir"]) / "regions"
    with (root / "manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)
    if manifest.get("version") != 1:
        raise ValueError("Unsupported regions manifest")
    if manifest.get("config_sha256") != config_hash(config):
        raise ValueError("Exam configuration changed; prepare a new region run")
    if manifest.get("pdf_sha256") != file_hash(resolve_path(config_dir, config["exam"]["scanned_pdf"])):
        raise ValueError("The scanned PDF changed; prepare a new region run")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != config["layout"]["pages_per_student"]:
        raise ValueError("Invalid region page manifest")
    for index, page in enumerate(pages):
        if file_hash(root / "images" / f"{index}.jpg") != page.get("sha256"):
            raise ValueError("A region image changed; prepare a new region run")
    return root, manifest


def approval_is_current(root, manifest, draft):
    try:
        with (root / "approval.json").open(encoding="utf-8") as file:
            approval = json.load(file)
        with (root / "annotations.json").open(encoding="utf-8") as file:
            annotations = json.load(file)
        return (
            approval["annotations_sha256"] == file_hash(root / "annotations.json")
            and approval["config_sha256"] == manifest["config_sha256"]
            and approval["pdf_sha256"] == manifest["pdf_sha256"]
            and approval["revision"] == draft["revision"]
            and annotations == draft["annotations"]
            and set(draft["reviewed"]) == set(range(len(manifest["pages"])))
        )
    except (OSError, KeyError, ValueError, TypeError):
        return False


def validate_annotations(annotations, pages, *, complete=False):
    if not isinstance(annotations, dict) or set(annotations) != {str(i) for i in range(len(pages))}:
        raise ValueError("Annotations must contain exactly the configured pages")
    labels = set()
    for index, dimensions in enumerate(pages):
        boxes = annotations[str(index)]
        if not isinstance(boxes, list) or len(boxes) > 500:
            raise ValueError("Each page must contain a list of at most 500 boxes")
        for box in boxes:
            if not isinstance(box, dict) or set(box) != {"x", "y", "width", "height", "label"}:
                raise ValueError("Each box requires x, y, width, height and label")
            if any(type(box[key]) is not int for key in ("x", "y", "width", "height")):
                raise ValueError("Box coordinates must be integer pixels")
            x, y, width, height = (box[k] for k in ("x", "y", "width", "height"))
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > dimensions["width"] or y + height > dimensions["height"]:
                raise ValueError(f"Box outside image bounds on page {index + 1}")
            label = box["label"]
            if not isinstance(label, str) or not label.strip() or len(label) > 64:
                raise ValueError("Every box needs a label of at most 64 characters")
            if label != label.strip() or label in {".", ".."} or any(
                char in "/\\" or ord(char) < 32 for char in label
            ):
                raise ValueError("Labels cannot contain path separators, control characters or surrounding spaces")
            normalized = label.strip().upper()
            if normalized in {"NAME", "ID", "CLASS"} and label != normalized:
                raise ValueError("Use exact uppercase labels NAME, ID and CLASS")
            if complete and normalized == "ID" and index != 0:
                raise ValueError("The ID box must be on the first page of each script")
            if normalized not in {"NAME", "ID", "CLASS"}:
                if normalized.startswith("Q") and len(normalized) > 1:
                    normalized = normalized[1:]
                try:
                    numeric = float(normalized)
                    if math.isfinite(numeric) and numeric.is_integer():
                        normalized = str(int(numeric))
                except ValueError:
                    pass
            if complete and normalized in labels:
                raise ValueError("Duplicate labels are not supported")
            labels.add(normalized)
    if complete:
        if not {"NAME", "ID", "CLASS"}.issubset(labels):
            raise ValueError("NAME, ID and CLASS boxes are required")
        if not labels - {"NAME", "ID", "CLASS"}:
            raise ValueError("At least one question box is required")
