from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with", "via", "using", "based", "toward", "towards"}
TOKEN_RE = re.compile(r"[a-z0-9]+")
SCHEMA = pa.schema([
    ("arxiv_id", pa.string()), ("partition", pa.string()),
    ("citation_key", pa.string()), ("groundtruth_arxiv_id", pa.string()),
    ("cand_1", pa.string()), ("cand_2", pa.string()), ("cand_3", pa.string()),
    ("cand_4", pa.string()), ("cand_5", pa.string()),
])


def tokens(text: str | None) -> set[str]:
    return {t for t in TOKEN_RE.findall((text or "").lower()) if t not in STOP and len(t) > 1}


def overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an automatic heuristic matching subset for arXiv BibTeX entries.")
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--fraction", type=float, default=0.10)
    parser.add_argument("--minimum-papers", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument("--shard-rows", type=int, default=50000)
    args = parser.parse_args()
    root = args.output / "export" / "parquet" / "data"
    bib_files = sorted((root / "bibtex").glob("*.parquet"))
    ref_files = sorted((root / "references").glob("*.parquet"))
    if not bib_files or not ref_files:
        parser.error("Expected bibtex and references Parquet tables first")

    ref_rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    ref_tokens: dict[str, dict[str, set[str]]] = defaultdict(dict)
    ref_author_tokens: dict[str, dict[str, set[str]]] = defaultdict(dict)
    for file in ref_files:
        pf = pq.ParquetFile(file)
        for batch in pf.iter_batches(batch_size=50000, columns=["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors"]):
            for row in batch.to_pylist():
                paper, rid = row["arxiv_id"], row["ref_arxiv_id"]
                if not paper or not rid:
                    continue
                old = ref_rows[paper].get(rid)
                title = row.get("ref_title") or ""
                if old is None or len(title) > len(old.get("ref_title") or ""):
                    ref_rows[paper][rid] = row
                    ref_tokens[paper][rid] = tokens(title)
                    ref_author_tokens[paper][rid] = tokens(" ".join(row.get("ref_authors") or []))

    bib_counts: Counter[str] = Counter()
    for file in bib_files:
        pf = pq.ParquetFile(file)
        for batch in pf.iter_batches(batch_size=100000, columns=["arxiv_id"]):
            for paper in batch.column(0).to_pylist():
                if paper:
                    bib_counts[paper] += 1
    paper_ids: set[str] = set()
    for file in sorted((root.parent / "data" / "papers").glob("*.parquet")):
        for batch in pq.ParquetFile(file).iter_batches(batch_size=50000, columns=["arxiv_id"]):
            paper_ids.update(x for x in batch.column(0).to_pylist() if x)
    manual_ledger = args.output / "matching" / "manual_labels.jsonl"
    manual_ids = set()
    if manual_ledger.exists():
        for line in manual_ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                manual_ids.add(json.loads(line)["arxiv_id"])
    assignment_population = paper_ids - manual_ids
    eligible = sorted(p for p, count in bib_counts.items() if count > 0 and p in assignment_population)
    if not eligible:
        raise SystemExit("No unlabelled publications with BibTeX entries are available")
    required_target = max(args.minimum_papers, math.ceil(args.fraction * len(assignment_population)))
    target = min(len(eligible), required_target)
    auto_papers = sorted(eligible, key=stable_key)[:target]
    auto_set = set(auto_papers)
    ordered_auto = sorted(auto_papers, key=stable_key)
    partitions = {p: "train" for p in ordered_auto}
    partitions[ordered_auto[0]] = "test"
    if len(ordered_auto) > 1:
        partitions[ordered_auto[1]] = "valid"

    out_dir = root / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("train-*-of-*.parquet"):
        old.unlink()
    expected_rows = sum(bib_counts[p] for p in auto_papers)
    total_shards = max(1, math.ceil(expected_rows / args.shard_rows))
    batch_rows = []
    written = 0
    match_count = 0
    shard_index = 0
    for file in bib_files:
        pf = pq.ParquetFile(file)
        for batch in pf.iter_batches(batch_size=50000, columns=["arxiv_id", "citation_key", "title", "authors"]):
            for entry in batch.to_pylist():
                paper = entry.get("arxiv_id")
                if paper not in auto_set:
                    continue
                title_tokens = tokens(entry.get("title"))
                author_tokens = tokens(" ".join(entry.get("authors") or []))
                candidates = []
                for rid, rrow in ref_rows[paper].items():
                    title_score = overlap(title_tokens, ref_tokens[paper][rid])
                    author_score = overlap(author_tokens, ref_author_tokens[paper][rid])
                    score = 0.85 * title_score + 0.15 * author_score
                    candidates.append((score, rid))
                candidates.sort(key=lambda item: (-item[0], item[1]))
                top = [rid for _, rid in candidates[:5]]
                top.extend([None] * (5 - len(top)))
                best_score = candidates[0][0] if candidates else 0.0
                groundtruth = candidates[0][1] if best_score >= args.threshold else None
                match_count += groundtruth is not None
                batch_rows.append({
                    "arxiv_id": paper, "partition": partitions[paper],
                    "citation_key": entry.get("citation_key") or "",
                    "groundtruth_arxiv_id": groundtruth,
                    **{f"cand_{i+1}": top[i] for i in range(5)},
                })
                written += 1
                if len(batch_rows) >= args.shard_rows:
                    path = out_dir / f"train-{shard_index:05d}-of-{total_shards:05d}.parquet"
                    pq.write_table(pa.Table.from_pylist(batch_rows, schema=SCHEMA), path, compression="zstd")
                    shard_index += 1
                    batch_rows.clear()
                    print(f"Wrote {written}/{expected_rows} prediction rows", flush=True)
    if batch_rows:
        path = out_dir / f"train-{shard_index:05d}-of-{total_shards:05d}.parquet"
        pq.write_table(pa.Table.from_pylist(batch_rows, schema=SCHEMA), path, compression="zstd")
        shard_index += 1
    if written != expected_rows:
        raise RuntimeError(f"Expected {expected_rows} rows but wrote {written}")

    readme = root.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    if text.startswith("---") and end >= 0:
        metadata = yaml.safe_load(text[3:end]) or {}
        configs = metadata.setdefault("configs", [])
        if not any(isinstance(c, dict) and c.get("config_name") == "predictions" for c in configs):
            configs.append({"config_name": "predictions", "data_files": [{"split": "train", "path": "data/predictions/*.parquet"}]})
        text = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip() + text[end:]
        readme.write_text(text, encoding="utf-8")
    summary = {
        "method": "automatic title-token Dice overlap (0.85) plus author-token overlap (0.15)",
        "automatic_match_threshold": args.threshold,
        "assignment_population_excluding_manual": len(assignment_population),
        "eligible_publications_with_bibtex": len(eligible),
        "required_automatic_publications": required_target,
        "automatic_publications": len(auto_papers),
        "automatic_fraction_of_remaining_assignment_pool": len(auto_papers) / max(1, len(assignment_population)),
        "automatic_fraction_of_eligible_publications": len(auto_papers) / max(1, len(eligible)),
        "automatic_requirement_met": len(auto_papers) >= required_target,
        "automatic_publication_ids": auto_papers,
        "manual_publications_labeled": len(manual_ids),
        "prediction_rows": written,
        "heuristic_matches": match_count,
        "partition_publications": {name: sum(value == name for value in partitions.values()) for name in ("train", "valid", "test")},
        "warning": "Automatic heuristic draft only. Required manual labels and supervised model/evaluation remain incomplete.",
    }
    report_dir = args.output / "matching"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "automatic_predictions_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "automatic_publication_ids"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


\n