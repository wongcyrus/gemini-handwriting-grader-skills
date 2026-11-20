"""Offline statistics from explicitly approved scores."""

import statistics

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference

from .grade_review import read_approved_grading
from .grading import safe_append
from .regions import atomic_json, file_hash
from .reporting import passing_policy, passing_rows, question_passing_policy, report_rows
from .rubric import question_key


def describe(scores):
    return [len(scores), statistics.mean(scores), statistics.median(scores),
            statistics.stdev(scores) if len(scores) > 1 else None, min(scores), max(scores)]


def question_boxplots(root, rows, questions):
    paths = []
    for start in range(0, len(questions), 20):
        selected = questions[start:start + 20]
        values = [[row["marks"][index] for row in rows]
                  for index in range(start, start + len(selected))]
        labels = [str(question["question_number"]) for question in selected]
        figure = Figure(figsize=(max(8, len(selected) * 0.6), 6), layout="constrained")
        FigureCanvasAgg(figure)
        axis = figure.subplots()
        axis.boxplot(values, tick_labels=labels, patch_artist=True)
        axis.set_title("Question score distributions")
        axis.set_xlabel("Question")
        axis.set_ylabel("Reviewed marks")
        axis.tick_params(axis="x", labelrotation=45)
        filename = f"question-boxplots-{len(paths) + 1:03}.png"
        figure.savefig(root / filename, dpi=150)
        figure.clear()
        paths.append(filename)
    return paths


def create_analytics(config, config_dir, config_path, *, run_id=None):
    validated = read_approved_grading(config, config_dir, config_path, run_id=run_id)
    grading_root, _, _, questions, _, _, _ = validated
    policy = passing_policy(config, questions)
    question_policy, question_inclusive = question_passing_policy(config, questions)
    review_hash = file_hash(grading_root / "review.xlsx")
    approval_hash = file_hash(grading_root / "approval.json")
    rows, _ = report_rows(validated)
    root = grading_root.parent / "analytics"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Analytics already exist; do not overwrite the existing run") from exc
    workbook = Workbook()
    overview = workbook.active
    overview.title = "Overall"
    overview.append(["Students", "Mean", "Median", "Sample standard deviation", "Minimum", "Maximum",
                     "Possible marks", "Pass mark", "Equality passes", "Pass count", "Pass rate (%)"])
    scores = [row["total"] for row in rows]
    passing = passing_rows(rows, *policy) if policy is not None else None
    safe_append(overview, [*describe(scores), sum(q["marks"] for q in questions),
                           policy[0] if policy else None, policy[1] if policy else None,
                           len(passing) if passing is not None else None,
                           100 * len(passing) / len(rows) if passing is not None else None])
    classes = workbook.create_sheet("Classes")
    classes.append(["Class", "Students", "Mean", "Median", "Sample standard deviation", "Minimum", "Maximum",
                    "Pass count", "Pass rate (%)"])
    for label in sorted({row["class"] for row in rows}):
        group = [row for row in rows if row["class"] == label]
        class_passing = passing_rows(group, *policy) if policy is not None else None
        safe_append(classes, [label, *describe([row["total"] for row in group]),
                              len(class_passing) if class_passing is not None else None,
                              100 * len(class_passing) / len(group) if class_passing is not None else None])
    sheet = workbook.create_sheet("Questions")
    sheet.append(["Question", "Students", "Mean", "Median", "Sample standard deviation", "Minimum", "Maximum",
                  "Possible marks", "Mean percent", "Pass mark", "Equality passes", "Pass count", "Pass rate (%)"])
    for index, question in enumerate(questions):
        values = [row["marks"][index] for row in rows]
        threshold = question_policy.get(question_key(question["question_number"]))
        count = (sum(value >= threshold if question_inclusive else value > threshold for value in values)
                 if threshold is not None else None)
        safe_append(sheet, [question["question_number"], *describe(values), question["marks"],
                            100 * statistics.mean(values) / question["marks"], threshold,
                            question_inclusive if threshold is not None else None, count,
                            100 * count / len(values) if count is not None else None])
    question_chart = BarChart()
    question_chart.title = "Mean score by question"
    question_chart.y_axis.title = "Marks"
    question_chart.add_data(Reference(sheet, min_col=3, min_row=1, max_row=len(questions) + 1), titles_from_data=True)
    question_chart.set_categories(Reference(sheet, min_col=1, min_row=2, max_row=len(questions) + 1))
    sheet.add_chart(question_chart, "K2")

    distribution = workbook.create_sheet("Distribution")
    distribution.append(["Score range (% of possible marks)", "Students"])
    maximum = sum(q["marks"] for q in questions)
    buckets = [0] * 10
    for score in scores:
        buckets[min(int(score / maximum * 10), 9)] += 1
    for index, count in enumerate(buckets):
        distribution.append([f"[{index * 10}, {(index + 1) * 10}{']' if index == 9 else ')'}", count])
    distribution_chart = BarChart()
    distribution_chart.title = "Total score distribution"
    distribution_chart.y_axis.title = "Students"
    distribution_chart.x_axis.title = "Percentage range"
    distribution_chart.add_data(Reference(distribution, min_col=2, min_row=1, max_row=11), titles_from_data=True)
    distribution_chart.set_categories(Reference(distribution, min_col=1, min_row=2, max_row=11))
    distribution.add_chart(distribution_chart, "D2")
    try:
        workbook.save(root / "statistics.xlsx")
    finally:
        workbook.close()
    boxplots = question_boxplots(root, rows, questions)
    read_approved_grading(config, config_dir, config_path, run_id=run_id)
    if file_hash(grading_root / "review.xlsx") != review_hash or file_hash(grading_root / "approval.json") != approval_hash:
        raise ValueError("Grading approval changed during analytics generation; discard the partial output")
    atomic_json(root / "manifest.json", {
        "version": 1, "completed": True, "review_sha256": review_hash, "grading_approval_sha256": approval_hash,
        "question_boxplots": [{"path": name, "sha256": file_hash(root / name)} for name in boxplots],
    })
    return root
