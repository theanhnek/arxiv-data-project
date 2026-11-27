"""Rebuild predictions for user-confirmed manual labels using the current reference table."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "via",
    "using",
    "based",
    "toward",
    "towards",
}
TOKEN = re.compile(r"[a-z0-9]+")
KNOWN_PARTS = {
    "2510.07077": "test",
    "2510.04978": "valid",
    "2510.06265": "train",
    "2510.08962": "train",
    "2510.09170": "train",
}
SCHEMA = pa.schema(
    [
        ("arxiv_id", pa.string()),
        ("partition", pa.string()),
        ("citation_key", pa.string()),
        ("groundtruth_arxiv_id", pa.string()),
        ("cand_1", pa.string()),
        ("cand_2", pa.string()),
        ("cand_3", pa.string()),
        ("cand_4", pa.string()),
        ("cand_5", pa.string()),
    ]
)


def tokens(text: str) -> set[str]:
    return {
        t
        for t in TOKEN.findall((text or "").lower())
        if t not in STOP and len(t) > 1
    }


def dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def assign_manual_partitions(manual_papers: Sequence[str]) -> Dict[str, str]:
    """Assign 1 per 5 manual publications to test, 1 per 5 to valid, and the rest to train."""
    unique_papers = sorted(set(manual_papers))
    if set(unique_papers).issubset(set(KNOWN_PARTS)):
        return {p: KNOWN_PARTS[p] for p in unique_papers}

    ordered = sorted(
        unique_papers,
        key=lambda p: hashlib.sha256(p.encode("utf-8")).hexdigest(),
    )
    k = max(1, len(ordered) // 5)
    partitions: Dict[str, str] = {}
    for idx, paper in enumerate(ordered):
        if idx < k:
            partitions[paper] = "test"
        elif idx < 2 * k and len(ordered) >= 2:
            partitions[paper] = "valid"
        else:
            partitions[paper] = "train"
    return partitions


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--review-csv",
        type=Path,
        default=None,
        help="Path to manual_label_review.csv (default: <output>/matching/manual_label_review.csv)",
    )
    parser.add_argument(
        "--expected-rows",
        type=int,
        default=None,
        help="Optional exact number of confirmed manual rows to enforce (e.g. 100)",
    )
    args = parser.parse_args(argv)

    root = args.output / "export" / "parquet" / "data"
    review_path = args.review_csv or (
        args.output / "matching" / "manual_label_review.csv"
    )
    if not review_path.is_file():
        raise SystemExit(f"Manual review CSV not found: {review_path}")

    with review_path.open(encoding="utf-8-sig", newline="") as f:
        confirmed = [
            r
            for r in csv.DictReader(f)
            if r.get("review_status", "").strip().upper()
            in ("CONFIRMED_BY_USER", "CONFIRMED")
        ]
    if not confirmed:
        raise SystemExit(
            f"No confirmed manual rows found in {review_path} (set review_status=CONFIRMED_BY_USER)"
        )
    if args.expected_rows is not None and len(confirmed) != args.expected_rows:
        raise SystemExit(
            f"Expected {args.expected_rows} confirmed manual rows, got {len(confirmed)}"
        )

    parts = assign_manual_partitions([r["arxiv_id"] for r in confirmed])
    wanted = {(r["arxiv_id"], r["citation_key"]): r for r in confirmed}
    bib = {}
    for path in sorted((root / "bibtex").glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=100_000,
            columns=["arxiv_id", "citation_key", "title", "authors"],
        ):
            for row in batch.to_pylist():
                key = (row.get("arxiv_id"), row.get("citation_key"))
                if key in wanted:
                    bib[key] = row

    refs: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in sorted((root / "references").glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=50_000,
            columns=["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors"],
        ):
            for row in batch.to_pylist():
                paper, rid = row.get("arxiv_id"), row.get("ref_arxiv_id")
                if paper not in parts or not rid:
                    continue
                old = refs[paper].get(rid)
                if old is None or len(row.get("ref_title") or "") > len(
                    old.get("ref_title") or ""
                ):
                    refs[paper][rid] = row

    rows = []
    ledger_rows: List[dict] = []
    missing_bib = []
    missing_truth = []
    for label in confirmed:
        paper, key = label["arxiv_id"], label["citation_key"]
        entry = bib.get((paper, key))
        if entry is None:
            missing_bib.append((paper, key))
            continue
        title_tokens = tokens(entry.get("title") or "")
        author_tokens = tokens(" ".join(entry.get("authors") or []))
        scored = []
        for rid, ref in refs[paper].items():
            score = 0.85 * dice(
                title_tokens, tokens(ref.get("ref_title") or "")
            ) + 0.15 * dice(
                author_tokens,
                tokens(" ".join(ref.get("ref_authors") or [])),
            )
            scored.append((score, rid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        candidates = [rid for _, rid in scored[:5]]
        truth_raw = (
            label.get("manual_groundtruth_arxiv_id")
            or label.get("suggested_groundtruth_arxiv_id")
            or "NONE"
        ).strip()
        truth = None if truth_raw.upper() == "NONE" or not truth_raw else truth_raw
        if truth and truth not in refs[paper]:
            missing_truth.append((paper, key, truth))
        candidates += [None] * (5 - len(candidates))
        rows.append(
            {
                "arxiv_id": paper,
                "partition": parts[paper],
                "citation_key": key,
                "groundtruth_arxiv_id": truth,
                **{f"cand_{i+1}": candidates[i] for i in range(5)},
            }
        )
        ledger_rows.append(
            {
                "arxiv_id": paper,
                "citation_key": key,
                "groundtruth_arxiv_id": truth,
                "manual_label": "NONE" if truth is None else truth,
                "label_source": "user_confirmed",
                "review_status": "confirmed",
            }
        )

    if missing_bib or missing_truth:
        raise SystemExit(
            json.dumps(
                {
                    "missing_bibtex": missing_bib[:10],
                    "missing_groundtruth_reference": missing_truth[:10],
                    "missing_bibtex_count": len(missing_bib),
                    "missing_groundtruth_count": len(missing_truth),
                }
            )
        )

    pred_dir = root / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    # Remove any partial manual-reviewed-*.parquet shards to prevent duplicate keys
    for stale in pred_dir.glob("manual-reviewed-*.parquet"):
        stale.unlink()

    out = pred_dir / "manual-reviewed-100.parquet"
    pq.write_table(
        pa.Table.from_pylist(rows, schema=SCHEMA), out, compression="zstd"
    )

    ledger_path = args.output / "matching" / "manual_labels.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "rows": len(rows),
                "manual_publications": len(parts),
                "candidate_source": "current reference table",
                "partition_rows": {
                    p: sum(r["partition"] == p for r in rows)
                    for p in ("train", "valid", "test")
                },
                "file": str(out),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
