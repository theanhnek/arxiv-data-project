"""Validate exported Hugging Face Parquet dataset before uploading.

Implemented for Group 14 — Part 3 (Pre-submission Dataset Validation).

Checks performed on a sharded Parquet dataset folder (default: ``data/export/parquet``):
1. Directory layout and Dataset Card (``README.md`` YAML ``configs:`` metadata).
2. Parquet readability, flat Arrow/Parquet schema compliance (no nested structs/maps),
   exact required columns and types for Milestone 1 tables (``papers``, ``references``)
   and optionally Milestone 2–3 tables (``elements``, ``hierarchy``, ``bibtex``, ``predictions``).
3. Row counts, shard counts, and missing-value statistics (null and empty checks),
   enforcing all non-null column constraints.
4. Assigned arXiv ID range enforcement using the official Group 14 ranges from
   ``[NM.KHDL] 24KHDL Project - Team Registration`` (no version suffixes, no out-of-range IDs).
5. Primary-key uniqueness (no duplicate ``arxiv_id`` in ``papers`` or duplicate
   ``(arxiv_id, ref_arxiv_id)`` in ``references``, etc.).
6. Cross-table referential integrity (every citing ``arxiv_id`` in child tables exists in ``papers``)
   and partition consistency in ``predictions`` when present.
7. Round-trip loading via Hugging Face ``datasets.load_dataset(...)`` to guarantee
   compatibility with the Hugging Face Hub Viewer and downstream consumers.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.discovery import (  # noqa: E402
    DEFAULT_MEMBER_QUOTA,
    expand_assigned_range,
    normalize_id,
)

# Official Group 14 ranges from "[NM.KHDL] 24KHDL Project - Team Registration" (cols H-K).
GROUP14_MEMBERS: Dict[str, Dict[str, Any]] = {
    "Kiet": {
        "index": 1,
        "full_name": "Lê Văn Tuấn Kiệt",
        "mssv": "22120171",
        "range": ("2025-10", 14401, "2025-10", 19400),
        "label": "2025-10 #14401–19400",
    },
    "Duong": {
        "index": 2,
        "full_name": "Huỳnh Võ Đại Dương",
        "mssv": "23120033",
        "range": ("2025-10", 9401, "2025-10", 14400),
        "label": "2025-10 #9401–14400",
    },
    "Ha": {
        "index": 3,
        "full_name": "Nguyễn Thanh Khánh Hà",
        "mssv": "23120037",
        "range": ("2025-10", 4401, "2025-10", 9400),
        "label": "2025-10 #4401–9400",
    },
    "Kien": {
        "index": 4,
        "full_name": "Đào Trung Kiên",
        "mssv": "23120055",
        "range": ("2025-09", 26047, "2025-10", 4400),
        "label": "2025-09 #26047 to 2025-10 #4400",
    },
    "Anh": {
        "index": 5,
        "full_name": "Nguyễn Hoàng Thế Anh",
        "mssv": "23120210",
        "range": ("2025-09", 11047, "2025-09", 16046),
        "label": "2025-09 #11047–16046",
    },
}

MEMBER_LOOKUP: Dict[str, str] = {}
for _slug, _info in GROUP14_MEMBERS.items():
    MEMBER_LOOKUP[_slug.lower()] = _slug
    MEMBER_LOOKUP[str(_info["index"])] = _slug
    MEMBER_LOOKUP[_info["mssv"]] = _slug


@dataclass(frozen=True)
class TableSpec:
    name: str
    milestone: int
    columns: Tuple[str, ...]
    non_null: Tuple[str, ...]
    list_str_columns: Tuple[str, ...]
    int_columns: Tuple[str, ...]
    primary_key: Tuple[str, ...]


TABLE_SPECS: Dict[str, TableSpec] = {
    "papers": TableSpec(
        name="papers",
        milestone=1,
        columns=(
            "arxiv_id",
            "title",
            "authors",
            "submission_date",
            "revised_dates",
            "venue",
        ),
        non_null=("arxiv_id",),
        list_str_columns=("authors", "revised_dates"),
        int_columns=(),
        primary_key=("arxiv_id",),
    ),
    "references": TableSpec(
        name="references",
        milestone=1,
        columns=(
            "arxiv_id",
            "ref_arxiv_id",
            "ref_title",
            "ref_authors",
            "ref_submission_date",
            "ref_s2_id",
        ),
        non_null=("arxiv_id", "ref_arxiv_id"),
        list_str_columns=("ref_authors",),
        int_columns=(),
        primary_key=("arxiv_id", "ref_arxiv_id"),
    ),
    "elements": TableSpec(
        name="elements",
        milestone=2,
        columns=("element_id", "arxiv_id", "kind", "content"),
        non_null=("element_id", "arxiv_id"),
        list_str_columns=(),
        int_columns=(),
        primary_key=("arxiv_id", "element_id"),
    ),
    "hierarchy": TableSpec(
        name="hierarchy",
        milestone=2,
        columns=("arxiv_id", "version", "element_id", "parent_id"),
        non_null=("arxiv_id", "version", "element_id", "parent_id"),
        list_str_columns=(),
        int_columns=("version",),
        primary_key=("arxiv_id", "version", "element_id", "parent_id"),
    ),
    "bibtex": TableSpec(
        name="bibtex",
        milestone=2,
        columns=(
            "arxiv_id",
            "citation_key",
            "entry_type",
            "title",
            "authors",
            "year",
            "doi",
            "journal",
            "url",
        ),
        non_null=("arxiv_id", "citation_key"),
        list_str_columns=("authors",),
        int_columns=("year",),
        primary_key=("arxiv_id", "citation_key"),
    ),
    "predictions": TableSpec(
        name="predictions",
        milestone=3,
        columns=(
            "arxiv_id",
            "partition",
            "citation_key",
            "groundtruth_arxiv_id",
            "cand_1",
            "cand_2",
            "cand_3",
            "cand_4",
            "cand_5",
        ),
        non_null=("arxiv_id", "partition", "citation_key"),
        list_str_columns=(),
        int_columns=(),
        primary_key=("arxiv_id", "citation_key"),
    ),
}

MILESTONE1_TABLES: Tuple[str, ...] = ("papers", "references")
ALL_SIX_TABLES: Tuple[str, ...] = (
    "papers",
    "references",
    "elements",
    "hierarchy",
    "bibtex",
    "predictions",
)
VALID_PARTITIONS = frozenset({"train", "valid", "test"})
VERSION_SUFFIX_RE = re.compile(r"v[1-9]\d*$", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def resolve_member_slug(member: str) -> str:
    key = member.strip().lower()
    if key not in MEMBER_LOOKUP:
        valid = ", ".join(GROUP14_MEMBERS.keys())
        raise ValueError(f"Unknown member {member!r}. Choose one of: {valid} (or 1..5)")
    return MEMBER_LOOKUP[key]


def build_allowed_ids(
    *,
    member: Optional[str] = None,
    assigned_range: Optional[Tuple[str, int, str, int]] = None,
    expected_total: int = DEFAULT_MEMBER_QUOTA,
) -> Tuple[Optional[Set[str]], Optional[str]]:
    """Return ``(allowed_base_ids_set, range_description)`` or ``(None, None)``."""
    if member is not None and assigned_range is not None:
        raise ValueError("Pass either --member or --range, not both")

    if member is not None:
        slug = resolve_member_slug(member)
        info = GROUP14_MEMBERS[slug]
        start_m, start_id, end_m, end_id = info["range"]
        ids = expand_assigned_range(
            start_m, start_id, end_m, end_id, expected_total=expected_total
        )
        return set(ids), f"{slug} ({info['full_name']} — {info['label']}, {len(ids)} IDs)"

    if assigned_range is not None:
        start_m, start_id, end_m, end_id = assigned_range
        ids = expand_assigned_range(
            start_m, start_id, end_m, end_id, expected_total=expected_total
        )
        return (
            set(ids),
            f"{start_m} #{start_id} to {end_m} #{end_id} ({len(ids)} IDs)",
        )

    return None, None


def _is_string_like(arrow_type: pa.DataType) -> bool:
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return True
    if pa.types.is_dictionary(arrow_type):
        return pa.types.is_string(arrow_type.value_type) or pa.types.is_large_string(
            arrow_type.value_type
        )
    return False


def _is_list_of_strings(arrow_type: pa.DataType) -> bool:
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        return _is_string_like(arrow_type.value_type)
    return False


def _is_nested_forbidden(arrow_type: pa.DataType) -> bool:
    if pa.types.is_struct(arrow_type) or pa.types.is_map(arrow_type):
        return True
    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        val = arrow_type.value_type
        return (
            pa.types.is_struct(val)
            or pa.types.is_map(val)
            or pa.types.is_list(val)
            or pa.types.is_large_list(val)
        )
    return False


def _extract_config_paths(data_files: Any) -> List[str]:
    paths: List[str] = []
    if isinstance(data_files, str):
        paths.append(data_files)
    elif isinstance(data_files, list):
        for item in data_files:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(item["path"])
    elif isinstance(data_files, dict):
        for val in data_files.values():
            paths.extend(_extract_config_paths(val))
    return paths


def _check_readme(
    dataset_dir: Path, required_tables: Sequence[str]
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    readme_path = dataset_dir / "README.md"
    info: Dict[str, Any] = {
        "exists": readme_path.is_file(),
        "path": str(readme_path),
        "declared_configs": [],
        "unresolved_placeholders": [],
    }
    if not readme_path.is_file():
        errors.append(f"Missing dataset card README.md at {readme_path}")
        return info, errors, warnings

    text = readme_path.read_text(encoding="utf-8")
    placeholders = sorted(set(PLACEHOLDER_RE.findall(text)))
    info["unresolved_placeholders"] = placeholders
    if placeholders:
        warnings.append(
            f"README.md still contains unresolved template placeholders: {', '.join(placeholders[:8])}"
        )

    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, flags=re.DOTALL)
    if not match:
        errors.append("README.md is missing valid YAML frontmatter (--- ... ---)")
        return info, errors, warnings

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        errors.append(f"Invalid YAML frontmatter in README.md: {exc}")
        return info, errors, warnings

    if not isinstance(frontmatter, dict) or not isinstance(frontmatter.get("configs"), list):
        errors.append("README.md YAML frontmatter must declare a `configs:` list")
        return info, errors, warnings

    configs_by_name: Dict[str, List[str]] = {}
    for entry in frontmatter["configs"]:
        if isinstance(entry, dict) and isinstance(entry.get("config_name"), str):
            cfg_name = entry["config_name"]
            configs_by_name[cfg_name] = _extract_config_paths(entry.get("data_files"))

    info["declared_configs"] = list(configs_by_name.keys())
    for table_name in required_tables:
        if table_name not in configs_by_name:
            errors.append(
                f"README.md `configs:` is missing entry for table '{table_name}'"
            )
            continue
        expected_glob = f"data/{table_name}/*.parquet"
        paths = configs_by_name[table_name]
        if not any(p.replace("\\", "/") == expected_glob for p in paths):
            errors.append(
                f"README.md config '{table_name}' data_files={paths!r} does not reference '{expected_glob}'"
            )

    return info, errors, warnings


def _validate_single_table(
    dataset_dir: Path,
    spec: TableSpec,
    allowed_ids: Optional[Set[str]],
    *,
    allow_empty_papers: bool = False,
) -> Tuple[Dict[str, Any], Set[str], List[str], List[str]]:
    """Validate one Parquet table folder and return ``(stats, seen_arxiv_ids, errors, warnings)``."""
    errors: List[str] = []
    warnings: List[str] = []
    table_dir = dataset_dir / "data" / spec.name
    shards = sorted(table_dir.glob("*.parquet")) if table_dir.is_dir() else []

    stats: Dict[str, Any] = {
        "table": spec.name,
        "directory": str(table_dir),
        "shards_count": len(shards),
        "rows_count": 0,
        "columns": [],
        "missing_values": {},
        "duplicate_keys_count": 0,
        "out_of_range_ids_count": 0,
        "invalid_arxiv_ids_count": 0,
    }
    seen_arxiv_ids: Set[str] = set()

    if not shards:
        errors.append(f"[{spec.name}] No Parquet shards found in {table_dir}")
        return stats, seen_arxiv_ids, errors, warnings

    arrow_tables: List[pa.Table] = []
    for shard in shards:
        try:
            arrow_tables.append(pq.read_table(shard))
        except Exception as exc:
            errors.append(f"[{spec.name}] Failed reading Parquet shard {shard.name}: {exc}")
            return stats, seen_arxiv_ids, errors, warnings

    # Verify shard schemas match
    base_schema = arrow_tables[0].schema
    for shard, tbl in zip(shards[1:], arrow_tables[1:]):
        if tbl.schema != base_schema:
            errors.append(f"[{spec.name}] Schema mismatch in shard {shard.name}")

    combined = pa.concat_tables(arrow_tables) if len(arrow_tables) > 1 else arrow_tables[0]
    stats["rows_count"] = combined.num_rows
    stats["columns"] = list(combined.column_names)

    # 1. Required columns & flat types check
    actual_cols = set(combined.column_names)
    missing_cols = [c for c in spec.columns if c not in actual_cols]
    extra_cols = [c for c in combined.column_names if c not in spec.columns]
    if missing_cols:
        errors.append(f"[{spec.name}] Missing required columns: {missing_cols}")
    if extra_cols:
        warnings.append(f"[{spec.name}] Extra columns outside specification: {extra_cols}")

    for field in base_schema:
        if _is_nested_forbidden(field.type):
            errors.append(
                f"[{spec.name}] Column '{field.name}' uses forbidden nested type: {field.type}"
            )
        if field.name in spec.list_str_columns and not _is_list_of_strings(field.type):
            errors.append(
                f"[{spec.name}] Column '{field.name}' must be list<string>, got {field.type}"
            )
        elif field.name in spec.int_columns and not pa.types.is_integer(field.type):
            errors.append(
                f"[{spec.name}] Column '{field.name}' must be integer, got {field.type}"
            )
        elif (
            field.name in spec.columns
            and field.name not in spec.list_str_columns
            and field.name not in spec.int_columns
            and not _is_string_like(field.type)
        ):
            errors.append(
                f"[{spec.name}] Column '{field.name}' must be string/dictionary, got {field.type}"
            )

    if spec.name == "papers" and combined.num_rows == 0 and not allow_empty_papers:
        errors.append("[papers] Table has 0 rows (expected collected papers)")

    if missing_cols:
        return stats, seen_arxiv_ids, errors, warnings

    # 2. Row-level checks: missing values, ID validity/range, primary-key duplicates
    rows = combined.to_pylist()
    total_rows = len(rows)

    missing_summary: Dict[str, Dict[str, Any]] = {}
    for col in spec.columns:
        null_count = 0
        empty_count = 0
        for row in rows:
            val = row.get(col)
            if val is None:
                null_count += 1
            elif isinstance(val, str) and not val.strip():
                empty_count += 1
            elif isinstance(val, list) and len(val) == 0:
                empty_count += 1
        missing_total = null_count + empty_count
        missing_summary[col] = {
            "null_count": null_count,
            "empty_count": empty_count,
            "missing_rate": round(missing_total / total_rows, 6) if total_rows else 0.0,
        }
        if col in spec.non_null and missing_total > 0:
            errors.append(
                f"[{spec.name}] Non-null column '{col}' has {null_count} nulls and {empty_count} empty values"
            )
    stats["missing_values"] = missing_summary

    seen_keys: Set[Tuple[Any, ...]] = set()
    dup_samples: List[Tuple[Any, ...]] = []
    out_of_range_samples: List[str] = []
    invalid_id_samples: List[str] = []
    dup_count = 0
    out_of_range_count = 0
    invalid_id_count = 0

    partition_by_paper: Dict[str, Set[str]] = {}

    for row in rows:
        # Primary key duplicate check
        key = tuple(row.get(k) for k in spec.primary_key)
        if key in seen_keys:
            dup_count += 1
            if len(dup_samples) < 10:
                dup_samples.append(key)
        else:
            seen_keys.add(key)

        # arXiv ID format & assigned range check
        raw_id = row.get("arxiv_id")
        if isinstance(raw_id, str) and raw_id.strip():
            cleaned = raw_id.strip()
            has_version_suffix = bool(VERSION_SUFFIX_RE.search(cleaned))
            try:
                canonical = normalize_id(cleaned)
            except ValueError:
                canonical = None

            if has_version_suffix or canonical is None or canonical != cleaned:
                invalid_id_count += 1
                if len(invalid_id_samples) < 10:
                    invalid_id_samples.append(cleaned)
            else:
                seen_arxiv_ids.add(canonical)
                if allowed_ids is not None and canonical not in allowed_ids:
                    out_of_range_count += 1
                    if len(out_of_range_samples) < 10:
                        out_of_range_samples.append(canonical)

        # Predictions-specific partition integrity
        if spec.name == "predictions":
            part = row.get("partition")
            if part not in VALID_PARTITIONS:
                errors.append(
                    f"[predictions] Invalid partition value {part!r} for arxiv_id={raw_id!r}"
                )
            elif isinstance(raw_id, str):
                partition_by_paper.setdefault(raw_id, set()).add(part)

    stats["duplicate_keys_count"] = dup_count
    stats["out_of_range_ids_count"] = out_of_range_count
    stats["invalid_arxiv_ids_count"] = invalid_id_count

    if dup_count > 0:
        errors.append(
            f"[{spec.name}] Found {dup_count} duplicate rows on key {spec.primary_key}. "
            f"Samples: {dup_samples[:5]}"
        )
    if invalid_id_count > 0:
        errors.append(
            f"[{spec.name}] Found {invalid_id_count} invalid or version-suffixed arxiv_id values. "
            f"Samples: {invalid_id_samples[:5]}"
        )
    if out_of_range_count > 0:
        errors.append(
            f"[{spec.name}] Found {out_of_range_count} arxiv_id values OUTSIDE assigned range! "
            f"Samples: {out_of_range_samples[:5]}"
        )

    if spec.name == "predictions":
        leaked = [pid for pid, parts in partition_by_paper.items() if len(parts) > 1]
        if leaked:
            errors.append(
                f"[predictions] {len(leaked)} publications span multiple partitions (leakage): {leaked[:5]}"
            )

    return stats, seen_arxiv_ids, errors, warnings


def _check_load_dataset(
    dataset_dir: Path,
    tables_to_check: Sequence[str],
    expected_rows: Dict[str, int],
    expected_cols: Dict[str, List[str]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Verify the Parquet folder loads cleanly via Hugging Face ``datasets.load_dataset``."""
    errors: List[str] = []
    result: Dict[str, Any] = {"checked": False, "tables": {}}
    try:
        from datasets import load_dataset
    except ImportError:
        errors.append(
            "Hugging Face `datasets` library is not installed. "
            "Install it (`pip install datasets`) or pass --skip-load-dataset."
        )
        return result, errors

    result["checked"] = True
    for table_name in tables_to_check:
        try:
            ds = load_dataset(str(dataset_dir.resolve()), name=table_name, split="train")
            loaded_rows = len(ds)
            loaded_cols = list(ds.column_names)
            ok = loaded_rows == expected_rows.get(table_name) and loaded_cols == expected_cols.get(
                table_name
            )
            result["tables"][table_name] = {
                "ok": ok,
                "rows_loaded": loaded_rows,
                "columns_loaded": loaded_cols,
            }
            if not ok:
                errors.append(
                    f"[load_dataset:{table_name}] Mismatch vs PyArrow: "
                    f"loaded rows={loaded_rows} (expected {expected_rows.get(table_name)}), "
                    f"cols={loaded_cols}"
                )
        except Exception as exc:
            result["tables"][table_name] = {"ok": False, "error": str(exc)}
            errors.append(f"[load_dataset:{table_name}] Failed to load dataset: {exc}")

    return result, errors


def validate_dataset(
    dataset_dir: Path,
    *,
    member: Optional[str] = None,
    assigned_range: Optional[Tuple[str, int, str, int]] = None,
    expected_total: int = DEFAULT_MEMBER_QUOTA,
    tables: Optional[Sequence[str]] = None,
    all_six_tables: bool = False,
    check_load_dataset: bool = True,
    allow_empty_papers: bool = False,
) -> Dict[str, Any]:
    """Run all pre-submission checks on ``dataset_dir`` and return a structured report."""
    dataset_dir = Path(dataset_dir)
    errors: List[str] = []
    warnings: List[str] = []

    allowed_ids, range_desc = build_allowed_ids(
        member=member, assigned_range=assigned_range, expected_total=expected_total
    )

    if all_six_tables:
        required_tables = list(ALL_SIX_TABLES)
    elif tables:
        unknown = [t for t in tables if t not in TABLE_SPECS]
        if unknown:
            raise ValueError(f"Unknown table name(s): {unknown}")
        required_tables = list(tables)
    else:
        required_tables = list(MILESTONE1_TABLES)
        # Also auto-validate any Milestone 2/3 tables that already exist under data/
        data_sub = dataset_dir / "data"
        for extra in ("elements", "hierarchy", "bibtex", "predictions"):
            if (data_sub / extra).is_dir() and any((data_sub / extra).glob("*.parquet")):
                required_tables.append(extra)

    if not dataset_dir.is_dir():
        errors.append(f"Dataset directory does not exist: {dataset_dir}")
        return {
            "passed": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "dataset_dir": str(dataset_dir),
            "assigned_range": range_desc,
            "required_tables": required_tables,
            "readme": {"exists": False},
            "tables": {},
            "load_dataset": {"checked": False, "tables": {}},
            "errors": errors,
            "warnings": warnings,
        }

    readme_info, readme_errors, readme_warnings = _check_readme(
        dataset_dir, required_tables
    )
    errors.extend(readme_errors)
    warnings.extend(readme_warnings)

    table_reports: Dict[str, Dict[str, Any]] = {}
    ids_by_table: Dict[str, Set[str]] = {}
    expected_rows: Dict[str, int] = {}
    expected_cols: Dict[str, List[str]] = {}

    for table_name in required_tables:
        spec = TABLE_SPECS[table_name]
        t_stats, t_ids, t_errors, t_warnings = _validate_single_table(
            dataset_dir,
            spec,
            allowed_ids,
            allow_empty_papers=allow_empty_papers,
        )
        table_reports[table_name] = t_stats
        ids_by_table[table_name] = t_ids
        expected_rows[table_name] = t_stats["rows_count"]
        expected_cols[table_name] = t_stats["columns"]
        errors.extend(t_errors)
        warnings.extend(t_warnings)

    # Cross-table referential integrity: child tables' arxiv_id must exist in `papers`
    if "papers" in ids_by_table and ids_by_table["papers"]:
        paper_ids = ids_by_table["papers"]
        for table_name, child_ids in ids_by_table.items():
            if table_name == "papers":
                continue
            orphans = sorted(child_ids - paper_ids)
            if orphans:
                errors.append(
                    f"[{table_name}] Found {len(orphans)} arxiv_id(s) not present in `papers`: "
                    f"{orphans[:5]}"
                )

    load_result: Dict[str, Any] = {"checked": False, "tables": {}}
    if check_load_dataset:
        valid_for_load = [
            t for t in required_tables if table_reports.get(t, {}).get("shards_count", 0) > 0
        ]
        if valid_for_load:
            load_result, load_errors = _check_load_dataset(
                dataset_dir, valid_for_load, expected_rows, expected_cols
            )
            errors.extend(load_errors)

    return {
        "passed": len(errors) == 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "assigned_range": range_desc,
        "required_tables": required_tables,
        "readme": readme_info,
        "tables": table_reports,
        "load_dataset": load_result,
        "errors": errors,
        "warnings": warnings,
    }


def _print_human_report(report: Dict[str, Any]) -> None:
    print("=" * 72)
    print("HUGGING FACE DATASET PRE-SUBMISSION VALIDATION REPORT")
    print("=" * 72)
    print(f"Dataset dir    : {report['dataset_dir']}")
    print(f"Assigned range : {report['assigned_range'] or '(not restricted — pass --member or --range)'}")
    print(f"Tables checked : {', '.join(report['required_tables'])}")
    print(f"README configs : {', '.join(report['readme'].get('declared_configs') or [])}")
    print("-" * 72)

    for name, t in report["tables"].items():
        print(
            f"Table `{name}`: rows={t['rows_count']}, shards={t['shards_count']}, "
            f"duplicates={t['duplicate_keys_count']}, "
            f"out_of_range={t['out_of_range_ids_count']}, "
            f"invalid_ids={t['invalid_arxiv_ids_count']}"
        )
        missing = t.get("missing_values") or {}
        for col, m in missing.items():
            if m["null_count"] or m["empty_count"]:
                pct = m["missing_rate"] * 100
                print(
                    f"  - missing `{col}`: null={m['null_count']}, "
                    f"empty={m['empty_count']} ({pct:.2f}%)"
                )

    load_info = report.get("load_dataset") or {}
    if load_info.get("checked"):
        print("-" * 72)
        for name, l_info in (load_info.get("tables") or {}).items():
            status = "OK" if l_info.get("ok") else f"FAIL ({l_info.get('error', 'mismatch')})"
            print(
                f"datasets.load_dataset('{name}'): {status} "
                f"(rows={l_info.get('rows_loaded', 0)})"
            )

    if report["warnings"]:
        print("-" * 72)
        for w in report["warnings"]:
            print(f"[WARNING] {w}")

    if report["errors"]:
        print("-" * 72)
        for err in report["errors"]:
            print(f"[ERROR]   {err}")

    print("=" * 72)
    print("RESULT:", "PASS — Ready for Hugging Face upload!" if report["passed"] else "FAIL — Fix errors before uploading!")
    print("=" * 72)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("data/export/parquet"),
        help="Root folder of the exported Parquet dataset (default: data/export/parquet)",
    )
    parser.add_argument(
        "--member",
        type=str,
        default=None,
        help="Group 14 member slug or index (Kiet/1, Duong/2, Ha/3, Kien/4, Anh/5)",
    )
    parser.add_argument(
        "--range",
        nargs=4,
        metavar=("START_MONTH", "START_ID", "END_MONTH", "END_ID"),
        default=None,
        help="Explicit assigned range, e.g. 2025-09 26047 2025-10 4400",
    )
    parser.add_argument(
        "--expected-total",
        type=int,
        default=DEFAULT_MEMBER_QUOTA,
        help="Total IDs per member range for cross-month calculation (default: 5000)",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        default=None,
        help="Specific tables to require (default: papers references, plus any existing M2/M3 tables)",
    )
    parser.add_argument(
        "--all-six-tables",
        action="store_true",
        help="Require all 6 final dataset tables (papers, references, elements, hierarchy, bibtex, predictions)",
    )
    parser.add_argument(
        "--skip-load-dataset",
        action="store_true",
        help="Skip Hugging Face `datasets.load_dataset` verification",
    )
    parser.add_argument(
        "--allow-empty-papers",
        action="store_true",
        help="Allow 0 rows in `papers` (useful only for schema-only dry runs)",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Optional output path to save the full JSON validation report",
    )
    args = parser.parse_args(argv)

    assigned_range = None
    if args.range is not None:
        try:
            assigned_range = (
                args.range[0],
                int(args.range[1]),
                args.range[2],
                int(args.range[3]),
            )
        except ValueError:
            parser.error("START_ID and END_ID in --range must be integers")

    try:
        report = validate_dataset(
            args.dataset_dir,
            member=args.member,
            assigned_range=assigned_range,
            expected_total=args.expected_total,
            tables=args.tables,
            all_six_tables=args.all_six_tables,
            check_load_dataset=not args.skip_load_dataset,
            allow_empty_papers=args.allow_empty_papers,
        )
    except ValueError as exc:
        parser.error(str(exc))

    _print_human_report(report)
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"JSON report saved to: {args.json_report}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
