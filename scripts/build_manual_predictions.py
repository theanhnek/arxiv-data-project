"""Rebuild predictions for user-confirmed labels using the current reference table."""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path("data/export/parquet/data")
REVIEW = Path("data/matching/manual_label_review.csv")
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with", "via", "using", "based", "toward", "towards"}
TOKEN = re.compile(r"[a-z0-9]+")
PARTS = {"2510.07077": "test", "2510.04978": "valid", "2510.06265": "train", "2510.08962": "train", "2510.09170": "train"}
SCHEMA = pa.schema([
    ("arxiv_id", pa.string()), ("partition", pa.string()), ("citation_key", pa.string()),
    ("groundtruth_arxiv_id", pa.string()), ("cand_1", pa.string()), ("cand_2", pa.string()),
    ("cand_3", pa.string()), ("cand_4", pa.string()), ("cand_5", pa.string()),
])


def tokens(text: str) -> set[str]:
    return {t for t in TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 1}


def dice(a: set[str], b: set[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def main() -> None:
    with REVIEW.open(encoding="utf-8-sig", newline="") as f:
        confirmed = [r for r in csv.DictReader(f) if r["review_status"] == "CONFIRMED_BY_USER"]
    if len(confirmed) != 100:
        raise SystemExit(f"Expected 100 confirmed manual rows, got {len(confirmed)}")

    wanted = {(r["arxiv_id"], r["citation_key"]): r for r in confirmed}
    bib = {}
    for path in sorted((ROOT / "bibtex").glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=100_000, columns=["arxiv_id", "citation_key", "title", "authors"]):
            for row in batch.to_pylist():
                key = (row.get("arxiv_id"), row.get("citation_key"))
                if key in wanted:
                    bib[key] = row

    refs: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in sorted((ROOT / "references").glob("*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000, columns=["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors"]):
            for row in batch.to_pylist():
                paper, rid = row.get("arxiv_id"), row.get("ref_arxiv_id")
                if paper not in PARTS or not rid:
                    continue
                old = refs[paper].get(rid)
                if old is None or len(row.get("ref_title") or "") > len(old.get("ref_title") or ""):
                    refs[paper][rid] = row

    rows = []
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
            score = .85 * dice(title_tokens, tokens(ref.get("ref_title") or "")) + .15 * dice(author_tokens, tokens(" ".join(ref.get("ref_authors") or [])))
            scored.append((score, rid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        candidates = [rid for _, rid in scored[:5]]
        truth_raw = label["manual_groundtruth_arxiv_id"].strip()
        truth = None if truth_raw.upper() == "NONE" else truth_raw
        if truth and truth not in refs[paper]:
            missing_truth.append((paper, key, truth))
        candidates += [None] * (5 - len(candidates))
        rows.append({
            "arxiv_id": paper, "partition": PARTS[paper], "citation_key": key,
            "groundtruth_arxiv_id": truth, **{f"cand_{i+1}": candidates[i] for i in range(5)},
        })

    if missing_bib or missing_truth:
        raise SystemExit(json.dumps({"missing_bibtex": missing_bib[:10], "missing_groundtruth_reference": missing_truth[:10], "missing_bibtex_count": len(missing_bib), "missing_groundtruth_count": len(missing_truth)}))
    out = ROOT / "predictions" / "manual-reviewed-100.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), out, compression="zstd")
    print(json.dumps({"rows": len(rows), "manual_publications": len(PARTS), "candidate_source": "current reference table", "partition_rows": {p: sum(r["partition"] == p for r in rows) for p in ("train", "valid", "test")}, "file": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
