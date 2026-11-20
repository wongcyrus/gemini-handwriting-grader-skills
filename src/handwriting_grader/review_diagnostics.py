"""Read-only, advisory diagnostics for a grading review workbook."""

import math
from collections import Counter

from openpyxl import load_workbook

from .attendance import present_roster
from .grade_review import confirmed, reviewed_rows, validate_review, verify_grading_run
from .regions import file_hash
from .rubric import question_key
from .run_context import run_directory


def diagnose_review(config, config_dir, config_path, *, run_id=None):
    results_path = run_directory(config, config_dir, run_id) / "grading" / "results.json"
    results_hash = file_hash(results_path)
    root, _, results, questions = verify_grading_run(config, config_dir, config_path, run_id=run_id)
    roster = present_roster(config, config_dir, config_path)
    expected_scripts = set(range(len(roster)))
    rubric = {question_key(q["question_number"]): q for q in questions}
    expected_marks = {(index, key) for index in expected_scripts for key in rubric}
    baseline = {(item["script_index"], question_key(item["question"])): item
                for item in results["marks"]}
    path = root / "review.xlsx"
    before = file_hash(path)
    issues = []
    workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        indices = []
        identities = []
        try:
            for row in reviewed_rows(workbook, "Scripts", ("script_index", "reviewed_id", "reviewed")):
                index = row["script_index"]
                if type(index) is not int or index not in expected_scripts:
                    issues.append("Scripts: invalid script index")
                    continue
                indices.append(index)
                if not confirmed(row["reviewed"]):
                    issues.append(f"Script {index}: identity not reviewed")
                student_id = str(row["reviewed_id"]).strip() if row["reviewed_id"] is not None else ""
                if student_id not in roster:
                    issues.append(f"Script {index}: missing or unknown roster ID")
                else:
                    identities.append(student_id)
            for index, count in Counter(indices).items():
                if count > 1:
                    issues.append(f"Script {index}: duplicate identity row")
            for index in sorted(expected_scripts - set(indices)):
                issues.append(f"Script {index}: missing identity row")
            duplicates = sum(count - 1 for count in Counter(identities).values() if count > 1)
            if duplicates:
                issues.append(f"Scripts: {duplicates} duplicate roster ID assignment(s)")
            missing = len(set(roster) - set(identities))
            if missing:
                issues.append(f"Scripts: {missing} present roster ID(s) not assigned")
        except ValueError as exc:
            issues.append(f"Scripts: {exc}")

        seen = set()
        try:
            for row in reviewed_rows(workbook, "Marks", (
                    "script_index", "question", "answer", "reasoning", "manual_mark", "reviewed")):
                index = row["script_index"]
                if type(index) is not int or row["question"] is None:
                    issues.append("Marks: invalid script index or question")
                    continue
                pair = (index, question_key(row["question"]))
                if pair not in expected_marks or pair in seen:
                    issues.append(f"Script {index}: unexpected or duplicate question row")
                    continue
                seen.add(pair)
                label = f"Script {index}, question {pair[1]}"
                if not confirmed(row["reviewed"]):
                    issues.append(f"{label}: mark not reviewed")
                original = baseline.get(pair)
                if original is None:
                    issues.append(f"{label}: missing raw grade")
                    continue
                answer = row["answer"] if row["answer"] is not None else ""
                reasoning = row["reasoning"] if row["reasoning"] is not None else ""
                if not isinstance(answer, str) or not isinstance(reasoning, str):
                    issues.append(f"{label}: answer and reasoning must be text")
                manual = row["manual_mark"]
                has_manual = manual is not None and str(manual).strip() != ""
                if (original["grading_failed"] or original["moderator_flag"]
                        or answer != original["answer"]) and not has_manual:
                    issues.append(f"{label}: manual mark required")
                mark = manual if has_manual else original["moderated_mark"]
                try:
                    numeric = float(mark) if not isinstance(mark, bool) else float("nan")
                except (TypeError, ValueError):
                    numeric = float("nan")
                if not math.isfinite(numeric) or not 0 <= numeric <= rubric[pair[1]]["marks"]:
                    issues.append(f"{label}: mark missing or outside allowed range")
            for index, key in sorted(expected_marks - seen):
                issues.append(f"Script {index}, question {key}: missing mark row")
        except ValueError as exc:
            issues.append(f"Marks: {exc}")
    finally:
        workbook.close()
    if file_hash(path) != before:
        raise ValueError("Review workbook changed during diagnostics")
    try:
        validate_review(config, config_dir, config_path, run_id=run_id)
    except ValueError as exc:
        issues.append(f"Approval validator: {exc}")
    if file_hash(path) != before or file_hash(results_path) != results_hash:
        raise ValueError("Review workbook or results changed during diagnostics")
    return issues
