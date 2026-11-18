"""Shared storage, coordinated by members 1 and 4.

Implements safe ID-to-folder mapping, JSON persistence and restart state.
Original IDs are kept in the metadata; folder names come from
:func:`src.downloader.safe_paper_dirname` so every module uses one mapping.
Only references with arXiv IDs enter the final ``references`` table.
Writes are atomic so interrupted runs never leave valid-looking partial files.

Layout used by the references stage (``<output>`` is ``--output``, default ``data``)::

    <output>/corpus/<safe_id>/references.json   publication-level cache, one per paper
    <output>/state/references.json              run report (summary + one entry per paper)
    <output>/export/papers.jsonl                final ``papers`` table (JSON Lines)
    <output>/export/references.jsonl            final ``references`` table (JSON Lines)

The references cache sits next to the ``v1/``, ``v2/`` version folders because
Semantic Scholar answers per publication and does not distinguish arXiv versions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Optional, TextIO

from .config import CrawlConfig
from .discovery import normalize_id
from .downloader import safe_paper_dirname

SCHEMA_VERSION = 1

# Outcome of one Semantic Scholar query for one publication.
STATUS_OK = "success"  # API answered and at least one reference was returned
STATUS_EMPTY = "empty_references"  # API answered, publication known, zero references returned
STATUS_NOT_FOUND = "not_found"  # API answered 404 for this publication
STATUS_FAILED = "failed"  # request error, throttling that did not clear, bad payload
STATUS_NOT_CRAWLED = "not_crawled"  # never queried (used by reports, never cached)
CACHEABLE_STATUSES = (STATUS_OK, STATUS_EMPTY, STATUS_NOT_FOUND)

# Columns of the two tables specified by Milestone 1.
PAPERS_COLUMNS = (
    "arxiv_id",
    "title",
    "authors",
    "submission_date",
    "revised_dates",
    "venue",
)
REFERENCES_COLUMNS = (
    "arxiv_id",
    "ref_arxiv_id",
    "ref_title",
    "ref_authors",
    "ref_submission_date",
    "ref_s2_id",
)

# Columns that hold a list of strings; every other column is a (nullable) string.
LIST_COLUMNS = frozenset({"authors", "revised_dates", "ref_authors"})
# Declared non-null by the Milestone 3 table specification.
NON_NULL_COLUMNS = frozenset({"arxiv_id", "ref_arxiv_id"})
DEFAULT_SHARD_ROWS = 50_000


# --------------------------------------------------------------------------- #
# Atomic persistence
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, writer: Callable[[TextIO], None]) -> None:
    """Write through a temp file in the same folder, then ``os.replace`` it into place.

    If ``writer`` raises (for example on a non-serializable value) the temp file is
    removed and the previous content of ``path`` stays untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path: Path, value: Any, *, indent: Optional[int] = 2) -> None:
    """Atomically write ``value`` as UTF-8 JSON. NaN/Infinity are rejected (invalid JSON)."""

    def writer(handle: TextIO) -> None:
        json.dump(value, handle, ensure_ascii=False, indent=indent, allow_nan=False)

    _atomic_write(path, writer)


def write_jsonl_atomic(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    """Atomically write one JSON object per line and return the number of rows."""
    count = 0

    def writer(handle: TextIO) -> None:
        nonlocal count
        count = 0
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False))
            handle.write("\n")
            count += 1

    _atomic_write(path, writer)
    return count


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file is missing, unreadable or invalid.

    Writes are atomic, so an invalid file means outside interference; callers treat
    it like a missing cache entry and simply fetch the data again.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# ID -> path mapping
# --------------------------------------------------------------------------- #
def paper_dirname(arxiv_id: str) -> str:
    """Safe single-component folder name for an arXiv ID (version suffix ignored)."""
    return safe_paper_dirname(normalize_id(arxiv_id))


def paper_dir(config: CrawlConfig, arxiv_id: str) -> Path:
    return config.output_dir / "corpus" / paper_dirname(arxiv_id)


def references_cache_path(config: CrawlConfig, arxiv_id: str) -> Path:
    return paper_dir(config, arxiv_id) / "references.json"


def references_report_path(config: CrawlConfig) -> Path:
    return config.output_dir / "state" / "references.json"


def discovery_path(config: CrawlConfig) -> Path:
    return config.output_dir / "state" / "discovery.json"


# --------------------------------------------------------------------------- #
# Discovery input
# --------------------------------------------------------------------------- #
def load_discovery_papers(path: Path) -> List[Dict[str, Any]]:
    """Return the papers of ``discovery.json`` whose metadata was resolved."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Discovery state file not found: {path}. "
            "Run discovery first (python -m src.main --discover ...)."
        )
    report = json.loads(path.read_text(encoding="utf-8"))
    return [p for p in report.get("papers", []) if p.get("status") == "resolved"]


# --------------------------------------------------------------------------- #
# References cache (one file per publication)
# --------------------------------------------------------------------------- #
def load_references_cache(config: CrawlConfig, arxiv_id: str) -> Optional[Dict[str, Any]]:
    """Return a valid cached record for ``arxiv_id`` or ``None``.

    A file that is corrupt, from another schema, or belongs to a different paper
    is ignored, so a bad cache never blocks or poisons a run.
    """
    base = normalize_id(arxiv_id)
    record = read_json(references_cache_path(config, base))
    if not isinstance(record, dict):
        return None
    if record.get("schema_version") != SCHEMA_VERSION or record.get("arxiv_id") != base:
        return None
    if record.get("status") not in CACHEABLE_STATUSES:
        return None
    if not isinstance(record.get("references"), list):
        return None
    return record


def save_references_cache(config: CrawlConfig, record: Dict[str, Any]) -> Path:
    """Atomically persist a definitive result. Failed results are never cached.

    Refusing ``failed`` here protects earlier good data: a later run that only hit
    a network error must not overwrite a previously cached list of references.
    """
    status = record.get("status")
    if status not in CACHEABLE_STATUSES:
        raise ValueError(f"Only definitive results are cached, got status={status!r}")
    path = references_cache_path(config, record["arxiv_id"])
    write_json_atomic(path, record, indent=None)
    return path


# --------------------------------------------------------------------------- #
# Final tables: papers and references (Milestone 1 specification)
# --------------------------------------------------------------------------- #
def build_papers_rows(
    config: CrawlConfig, *, discovery_file: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """One ``papers`` row per resolved publication.

    ``revised_dates`` mirrors discovery and may be empty: the arXiv Atom API does
    not return every revision date (see ``revision_dates_complete`` in discovery).
    """
    papers = load_discovery_papers(discovery_file or discovery_path(config))
    rows: List[Dict[str, Any]] = []
    for paper in papers:
        rows.append(
            {
                "arxiv_id": paper["arxiv_id"],
                "title": paper.get("title") or None,
                "authors": [a for a in (paper.get("authors") or []) if a],
                "submission_date": paper.get("submission_date") or None,
                "revised_dates": list(paper.get("revised_dates") or []),
                "venue": paper.get("venue") or None,
            }
        )
    return rows


def build_references_rows(
    config: CrawlConfig, *, discovery_file: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """One ``references`` row per crawled reference that carries an arXiv ID.

    References without an arXiv ID stay in the local cache but are not exported.
    A reference listed twice for the same citing paper yields a single row.
    ``ref_submission_date`` is only filled when an arXiv submission date is known;
    Semantic Scholar's publication date or year is never used as a substitute.
    """
    papers = load_discovery_papers(discovery_file or discovery_path(config))
    rows: List[Dict[str, Any]] = []
    for paper in papers:
        cached = load_references_cache(config, paper["arxiv_id"])
        if cached is None or cached["status"] != STATUS_OK:
            continue
        seen = set()
        for ref in cached["references"]:
            ref_id = ref.get("arxiv_id")
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            rows.append(
                {
                    "arxiv_id": paper["arxiv_id"],
                    "ref_arxiv_id": ref_id,
                    "ref_title": ref.get("title"),
                    "ref_authors": list(ref.get("authors") or []),
                    "ref_submission_date": ref.get("submission_date"),
                    "ref_s2_id": ref.get("s2_id"),
                }
            )
    return rows


def export_tables(
    config: CrawlConfig,
    *,
    export_dir: Optional[Path] = None,
    discovery_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write ``papers.jsonl`` and ``references.jsonl`` and return their row counts.

    JSON Lines needs no extra dependency; ``export_parquet`` writes the sharded
    Parquet tables that are uploaded to Hugging Face.
    """
    target = Path(export_dir) if export_dir else config.output_dir / "export"
    papers_rows = build_papers_rows(config, discovery_file=discovery_file)
    reference_rows = build_references_rows(config, discovery_file=discovery_file)
    papers_path = target / "papers.jsonl"
    references_path = target / "references.jsonl"
    write_jsonl_atomic(papers_path, papers_rows)
    write_jsonl_atomic(references_path, reference_rows)
    return {
        "papers_rows": len(papers_rows),
        "references_rows": len(reference_rows),
        "papers_path": str(papers_path),
        "references_path": str(references_path),
    }


_DATASET_CARD = """---
configs:
- config_name: papers
  data_files: "data/papers/*.parquet"
- config_name: references
  data_files: "data/references/*.parquet"
---

# arXiv papers and references

Tables exported by the group 14 pipeline for the assigned arXiv ID range.

- `papers`: arxiv_id, title, authors, submission_date, revised_dates, venue
- `references`: arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id

Only references that carry an arXiv ID are listed in `references`.
"""


def _require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Parquet export needs pyarrow: pip install pyarrow (see src/requirements.txt)"
        ) from exc
    return pa, pq


def write_parquet_shards(
    rows: List[Dict[str, Any]],
    columns: Iterable[str],
    directory: Path,
    *,
    shard_rows: int = DEFAULT_SHARD_ROWS,
    prefix: str = "train",
) -> List[Path]:
    """Write ``rows`` as ``<prefix>-00000-of-0000N.parquet`` files in ``directory``.

    An empty table still produces one shard so the schema is preserved. Shards are
    written to temporary names first and the previous run's shards are removed only
    after every new shard exists, so an interrupted export never leaves a mix of old
    and new files with inconsistent ``of-N`` counts.
    """
    if shard_rows < 1:
        raise ValueError("shard_rows must be >= 1")
    pa, pq = _require_pyarrow()
    columns = tuple(columns)
    schema = pa.schema(
        [
            pa.field(
                c,
                pa.list_(pa.string()) if c in LIST_COLUMNS else pa.string(),
                nullable=c not in NON_NULL_COLUMNS,
            )
            for c in columns
        ]
    )
    chunks = [rows[i : i + shard_rows] for i in range(0, len(rows), shard_rows)] or [[]]
    total = len(chunks)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    staged: List[tuple] = []
    try:
        for index, chunk in enumerate(chunks):
            final = directory / f"{prefix}-{index:05d}-of-{total:05d}.parquet"
            tmp = final.with_name(final.name + ".tmp")
            table = pa.Table.from_pylist(
                [{c: row.get(c) for c in columns} for row in chunk], schema=schema
            )
            pq.write_table(table, tmp)
            staged.append((tmp, final))
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise
    for old in directory.glob("*.parquet"):
        old.unlink()
    for tmp, final in staged:
        os.replace(tmp, final)
    return [final for _, final in staged]


def export_parquet(
    config: CrawlConfig,
    *,
    export_dir: Optional[Path] = None,
    discovery_file: Optional[Path] = None,
    shard_rows: int = DEFAULT_SHARD_ROWS,
) -> Dict[str, Any]:
    """Write sharded Parquet tables laid out like the Hugging Face dataset repository.

    ``<export>/parquet/`` is the repository root: ``data/papers/*.parquet``,
    ``data/references/*.parquet`` and a ``README.md`` dataset card (written only if
    none exists) that declares one config per table. Uploading that folder as is gives
    the structure required by the Milestone 3 specification.
    """
    root = (Path(export_dir) if export_dir else config.output_dir / "export") / "parquet"
    papers_rows = build_papers_rows(config, discovery_file=discovery_file)
    reference_rows = build_references_rows(config, discovery_file=discovery_file)
    papers_shards = write_parquet_shards(
        papers_rows, PAPERS_COLUMNS, root / "data" / "papers", shard_rows=shard_rows
    )
    reference_shards = write_parquet_shards(
        reference_rows,
        REFERENCES_COLUMNS,
        root / "data" / "references",
        shard_rows=shard_rows,
    )
    card = root / "README.md"
    if not card.exists():
        card.write_text(_DATASET_CARD, encoding="utf-8")
    return {
        "papers_rows": len(papers_rows),
        "references_rows": len(reference_rows),
        "papers_shards": [str(p) for p in papers_shards],
        "references_shards": [str(p) for p in reference_shards],
        "folder": str(root),
    }
