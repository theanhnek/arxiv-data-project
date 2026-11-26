"""Aggregate per-member benchmark_summary.json files into CSV and Markdown."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


MEMBERS = (
    ("Kiet", "Kiệt", "2025-10 #14401–19400"),
    ("Duong", "Dương", "2025-10 #9401–14400"),
    ("Ha", "Hà", "2025-10 #4401–9400"),
    ("Kien", "Kiên", "2025-09 #26047 to 2025-10 #4400"),
    ("Anh", "Anh", "2025-09 #11047–16046"),
)

COLUMNS = (
    "member",
    "assigned_range",
    "run_date",
    "git_commit",
    "ids_assigned",
    "papers_resolved",
    "versions_downloaded",
    "versions_extraction_completed",
    "versions_expected",
    "discovery_seconds",
    "download_seconds",
    "extraction_seconds",
    "references_seconds",
    "end_to_end_seconds",
    "peak_ram_mb",
    "avg_ram_mb",
    "peak_working_disk_mb",
    "final_output_disk_mb",
    "final_corpus_disk_mb",
    "dataset_url",
    "metrics_file",
    "status",
)


def _at(data: Dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _format_cell(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def _read_summary(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    if not isinstance(summary, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return summary


def _make_row(
    slug: str,
    name: str,
    assigned_range: str,
    metrics_dir: Path,
    dataset_urls: Dict[str, str],
) -> Dict[str, Any]:
    path = metrics_dir / slug / "benchmark_summary.json"
    summary = _read_summary(path)
    row: Dict[str, Any] = {column: "" for column in COLUMNS}
    row.update(
        member=name,
        assigned_range=assigned_range,
        dataset_url=dataset_urls.get(slug, ""),
        metrics_file=str(path),
        status="missing_metrics",
    )
    if summary is None:
        return row

    discovery = summary.get("discovery") or {}
    download_extract = summary.get("download_and_extraction") or {}
    performance = summary.get("performance") or {}
    resources = performance.get("resources") or {}
    completed = int(download_extract.get("extracted_tex_versions") or 0) + int(
        download_extract.get("no_tex_source_versions") or 0
    )
    expected_versions = int(download_extract.get("expected_versions") or 0)

    row.update(
        run_date=summary.get("generated_at", ""),
        git_commit=_at(summary, "environment", "git_commit") or "",
        ids_assigned=discovery.get("assigned_unique_ids", ""),
        papers_resolved=discovery.get("resolved_papers", ""),
        versions_downloaded=download_extract.get("downloaded_versions", ""),
        versions_extraction_completed=(
            f"{completed}/{expected_versions}" if expected_versions else ""
        ),
        versions_expected=expected_versions if expected_versions else "",
        discovery_seconds=performance.get("discovery_elapsed_seconds", ""),
        download_seconds=performance.get("download_elapsed_seconds", ""),
        extraction_seconds=performance.get("extraction_elapsed_seconds", ""),
        references_seconds=performance.get("references_elapsed_seconds", ""),
        end_to_end_seconds=performance.get("end_to_end_wall_time_seconds", ""),
        peak_ram_mb=resources.get("peak_ram_mb", ""),
        avg_ram_mb=resources.get("avg_ram_mb", ""),
        peak_working_disk_mb=resources.get("peak_working_disk_mb", ""),
        final_output_disk_mb=resources.get("final_output_disk_mb", ""),
        final_corpus_disk_mb=resources.get("final_corpus_disk_mb", ""),
        status="loaded",
    )
    return row


def _parse_dataset_urls(values: Iterable[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Expected SLUG=URL for --dataset-url, got {value!r}")
        slug, url = value.split("=", 1)
        if slug not in {member[0] for member in MEMBERS}:
            raise ValueError(f"Unknown member slug {slug!r}")
        result[slug] = url
    return result


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = (
        "member",
        "assigned_range",
        "run_date",
        "ids_assigned",
        "papers_resolved",
        "versions_downloaded",
        "versions_extraction_completed",
        "discovery_seconds",
        "download_seconds",
        "extraction_seconds",
        "references_seconds",
        "end_to_end_seconds",
        "peak_ram_mb",
        "avg_ram_mb",
        "peak_working_disk_mb",
        "final_output_disk_mb",
        "final_corpus_disk_mb",
        "dataset_url",
        "status",
    )
    labels = (
        "Member",
        "Assigned range",
        "Run date",
        "IDs assigned",
        "Papers resolved",
        "Versions downloaded",
        "Extraction complete / expected",
        "Discovery (s)",
        "Download (s)",
        "Extraction (s)",
        "References (s)",
        "End-to-end (s)",
        "Peak RAM (MB)",
        "Average RAM (MB)",
        "Peak working disk (MB)",
        "Final output disk (MB)",
        "Final corpus disk (MB)",
        "Dataset URL",
        "Status",
    )
    lines = [
        "# Group 14 Member Runtime Summary",
        "",
        "Values are copied from each member's `benchmark_summary.json`. Blank cells mean the member's metrics file has not been provided.",
        "",
        "| " + " | ".join(labels) + " |",
        "| " + " | ".join("---" for _ in labels) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_format_cell(row.get(c)) for c in columns) + " |")

    loaded = [row for row in rows if row["status"] == "loaded"]
    cumulative = sum(float(row["end_to_end_seconds"] or 0) for row in loaded)
    assigned_total = sum(int(row["ids_assigned"] or 0) for row in loaded)
    resolved_total = sum(int(row["papers_resolved"] or 0) for row in loaded)
    lines.extend(
        [
            "",
            "## Aggregate notes",
            "",
            f"- Metrics received: {len(loaded)} / {len(rows)} members.",
            f"- Assigned IDs represented by received metrics: {assigned_total}.",
            f"- Resolved papers represented by received metrics: {resolved_total}.",
            f"- Cumulative member processing time: {_format_cell(cumulative)} seconds. This is the sum of member durations, not group elapsed wall time.",
            "- RAM and disk are reported per member because runs may use different machines; do not sum those values.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metrics-dir",
        type=Path,
        default=Path("data/team_metrics"),
        help="Folder containing <MemberSlug>/benchmark_summary.json files",
    )
    parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("data/metrics/group_runtime_summary.csv"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("data/metrics/group_runtime_summary.md"),
    )
    parser.add_argument(
        "--dataset-url",
        action="append",
        default=[],
        metavar="SLUG=URL",
        help="Optional public URL, e.g. Ha=https://huggingface.co/datasets/hayez0311/name",
    )
    args = parser.parse_args()

    try:
        dataset_urls = _parse_dataset_urls(args.dataset_url)
        rows = [
            _make_row(slug, name, assigned_range, args.metrics_dir, dataset_urls)
            for slug, name, assigned_range in MEMBERS
        ]
        _write_csv(args.csv_output, rows)
        _write_markdown(args.markdown_output, rows)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"CSV summary: {args.csv_output}")
    print(f"Markdown summary: {args.markdown_output}")
    print(f"Metrics received: {sum(row['status'] == 'loaded' for row in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
