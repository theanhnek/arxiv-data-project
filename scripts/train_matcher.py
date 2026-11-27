from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
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
TOKEN_RE = re.compile(r"[a-z0-9]+")
FEATURES = (
    "title_dice",
    "title_jaccard",
    "bigram_dice",
    "author_dice",
    "title_exact",
    "length_ratio",
    "year_similarity",
    "baseline_rank",
)
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


def toks(s: Optional[str]) -> List[str]:
    s = (s or "").lower()
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    return [x for x in TOKEN_RE.findall(s) if x not in STOP and len(x) > 1]


def dice(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return (2.0 * len(sa & sb) / (len(sa) + len(sb))) if (sa and sb) else 0.0


def bigrams(x: Sequence[str]) -> List[str]:
    return [x[i] + " " + x[i + 1] for i in range(len(x) - 1)]


def year_from(value: Any) -> Optional[int]:
    m = re.search(r"(19|20|21)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


def feature_vector(
    entry: Dict[str, Any], ref: Dict[str, Any], rank: int
) -> np.ndarray:
    a = toks(entry.get("title"))
    b = toks(ref.get("ref_title"))
    aset, bset = set(a), set(b)
    union = aset | bset
    title_dice = dice(a, b)
    jaccard = len(aset & bset) / len(union) if union else 0.0
    ba, bb = bigrams(a), bigrams(b)
    bd = dice(ba, bb)
    aa = toks(" ".join(entry.get("authors") or []))
    ab = toks(" ".join(ref.get("ref_authors") or []))
    author_dice = dice(aa, ab)
    exact = float(" ".join(a) == " ".join(b) and bool(a) and bool(b))
    length_ratio = min(len(a), len(b)) / max(len(a), len(b)) if a and b else 0.0
    y1 = entry.get("year")
    y2 = year_from(ref.get("ref_submission_date"))
    year_similarity = (
        max(0.0, 1.0 - abs(int(y1) - y2) / 10.0) if y1 and y2 else 0.0
    )
    return np.asarray(
        [
            title_dice,
            jaccard,
            bd,
            author_dice,
            exact,
            length_ratio,
            year_similarity,
            1.0 / rank,
        ],
        dtype=np.float32,
    )


def sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def mrr(
    rows: Sequence[Tuple[int, Dict[str, Any]]], ranked: Sequence[Sequence[str]]
) -> float:
    if not rows:
        return 0.0
    total = 0.0
    for idx, row in rows:
        gt = row.get("groundtruth_arxiv_id")
        if gt:
            for rank, rid in enumerate(ranked[idx][:5], 1):
                if rid == gt:
                    total += 1.0 / rank
                    break
    return total / len(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Train a logistic pair ranker and evaluate MRR@5."
    )
    ap.add_argument("--output", type=Path, default=Path("data"))
    ap.add_argument("--epochs", type=int, default=250)
    ap.add_argument("--learning-rate", type=float, default=0.15)
    ap.add_argument("--l2", type=float, default=0.02)
    ap.add_argument("--negative-per-entry", type=int, default=2)
    ap.add_argument(
        "--max-candidates-per-entry",
        type=int,
        default=25,
        help="Max candidates from the publication's m x n reference pool to score per entry",
    )
    ap.add_argument("--seed", type=int, default=14)
    args = ap.parse_args(argv)
    _ = np.random.default_rng(args.seed)
    base = args.output / "export" / "parquet" / "data"
    pred_files = sorted((base / "predictions").glob("*.parquet"))
    bib_files = sorted((base / "bibtex").glob("*.parquet"))
    ref_files = sorted((base / "references").glob("*.parquet"))
    pred_rows = []
    for f in pred_files:
        for b in pq.ParquetFile(f).iter_batches(
            batch_size=50000, columns=SCHEMA.names
        ):
            pred_rows.extend(b.to_pylist())
    task_ids = {r["arxiv_id"] for r in pred_rows}
    bib_meta = {}
    for f in bib_files:
        for b in pq.ParquetFile(f).iter_batches(
            batch_size=100000,
            columns=["arxiv_id", "citation_key", "title", "authors", "year"],
        ):
            for r in b.to_pylist():
                if r.get("arxiv_id") in task_ids:
                    bib_meta[(r["arxiv_id"], r["citation_key"])] = r
    refs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    refs_by_paper: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for f in ref_files:
        for b in pq.ParquetFile(f).iter_batches(
            batch_size=50000,
            columns=[
                "arxiv_id",
                "ref_arxiv_id",
                "ref_title",
                "ref_authors",
                "ref_submission_date",
            ],
        ):
            for r in b.to_pylist():
                paper = r.get("arxiv_id")
                rid = r.get("ref_arxiv_id")
                if paper and rid and paper in task_ids:
                    old = refs.get((paper, rid))
                    if old is None or len(r.get("ref_title") or "") > len(
                        old.get("ref_title") or ""
                    ):
                        refs[(paper, rid)] = r
                        refs_by_paper[paper][rid] = r

    manual = set()
    ledger = args.output / "matching" / "manual_labels.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                manual.add((r["arxiv_id"], r["citation_key"]))
    if not pred_rows:
        raise SystemExit("No prediction rows found")
    if not manual:
        # Fallback: treat all rows if no manual ledger exists yet (e.g., pilot smoke test)
        manual = {(r["arxiv_id"], r["citation_key"]) for r in pred_rows}

    row_features = []
    candidate_ids = []
    baseline_ranked = []
    train_x = []
    train_y = []
    train_w = []
    by_partition: Counter = Counter()
    manual_counts: Counter = Counter()
    missing_bib = []
    missing_refs = 0

    for idx, row in enumerate(pred_rows):
        paper_id = row["arxiv_id"]
        key = (paper_id, row["citation_key"])
        entry = bib_meta.get(key)
        if entry is None:
            missing_bib.append(key)
            entry = {"title": "", "authors": [], "year": None}
        is_manual = key in manual
        by_partition[row["partition"]] += 1
        if is_manual:
            manual_counts[row["partition"]] += 1

        # Build candidate pool from the publication's m x n reference space
        existing_cids = [
            row.get(f"cand_{i}") for i in range(1, 6) if row.get(f"cand_{i}")
        ]
        paper_pool = refs_by_paper.get(paper_id, {})
        if is_manual or len(paper_pool) <= args.max_candidates_per_entry:
            # Score full reference pool of the publication in baseline similarity order
            entry_t = set(toks(entry.get("title")))
            entry_a = set(toks(" ".join(entry.get("authors") or [])))
            scored_pool = []
            for rid, ref_obj in paper_pool.items():
                rt = set(toks(ref_obj.get("ref_title")))
                ra = set(toks(" ".join(ref_obj.get("ref_authors") or [])))
                td = (
                    2.0 * len(entry_t & rt) / (len(entry_t) + len(rt))
                    if (entry_t and rt)
                    else 0.0
                )
                ad = (
                    2.0 * len(entry_a & ra) / (len(entry_a) + len(ra))
                    if (entry_a and ra)
                    else 0.0
                )
                scored_pool.append((0.85 * td + 0.15 * ad, rid))
            scored_pool.sort(key=lambda item: (-item[0], item[1]))
            cids = [
                rid for _, rid in scored_pool[: args.max_candidates_per_entry]
            ]
            for ec in existing_cids:
                if ec not in cids:
                    cids.append(ec)
            truth_id = row.get("groundtruth_arxiv_id")
            if (
                row["partition"] == "train"
                and truth_id
                and truth_id in paper_pool
                and truth_id not in cids
            ):
                cids.append(truth_id)
        else:
            cids = existing_cids

        feats = []
        valid = []
        for rank, rid in enumerate(cids, 1):
            if not rid:
                continue
            ref = refs.get((paper_id, rid))
            if ref is None:
                missing_refs += 1
                continue
            feats.append(feature_vector(entry, ref, rank))
            valid.append(rid)

        row_features.append(
            np.stack(feats)
            if feats
            else np.zeros((0, len(FEATURES)), dtype=np.float32)
        )
        candidate_ids.append(valid)
        baseline_ranked.append(valid[:5])
        if row["partition"] != "train" or not feats:
            continue
        truth = row.get("groundtruth_arxiv_id")
        labels = np.asarray(
            [int(bool(truth) and rid == truth) for rid in valid], dtype=np.int8
        )
        pos = np.flatnonzero(labels)
        keep = set(pos.tolist())
        if len(keep) == 0:
            n = min(args.negative_per_entry, len(valid))
            keep.update(range(n))
        else:
            keep.update(range(min(len(valid), args.negative_per_entry + 3)))
        base_weight = 1.0 if is_manual else 0.35
        for j in sorted(keep):
            train_x.append(feats[j])
            train_y.append(labels[j])
            train_w.append(base_weight * (3.0 if labels[j] else 1.0))

    if missing_bib:
        print(
            f"Warning: {len(missing_bib)} prediction entries have no matching BibTeX metadata"
        )
    if missing_refs:
        print(
            f"Warning: {missing_refs} candidate IDs have no reference metadata"
        )
    if not train_x:
        raise SystemExit("No training candidate pairs were created")
    X = np.vstack(train_x).astype(np.float64)
    y = np.asarray(train_y, dtype=np.float64)
    sw = np.asarray(train_w, dtype=np.float64)
    if not np.any(y == 1):
        raise SystemExit("Training data has no positive candidate labels")
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-8] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1], dtype=np.float64)
    bias = 0.0
    total_w = sw.sum()
    for _ in range(args.epochs):
        p = sigmoid(Z @ w + bias)
        err = (p - y) * sw
        gw = (Z.T @ err) / total_w + args.l2 * w
        gb = err.sum() / total_w
        w -= args.learning_rate * gw
        bias -= args.learning_rate * gb

    ranked = []
    model_scores = []
    for i, features in enumerate(row_features):
        if len(features) == 0:
            ranked.append([])
            model_scores.append([])
            continue
        z = (features - mu) / sd
        scores = (z @ w + bias).tolist()
        order = sorted(range(len(scores)), key=lambda j: (-scores[j], j))
        ranked.append([candidate_ids[i][j] for j in order[:5]])
        model_scores.append(scores)

    def group(part: Optional[str] = None, manual_only: Optional[bool] = None):
        result = []
        for i, r in enumerate(pred_rows):
            if part and r["partition"] != part:
                continue
            is_manual = (r["arxiv_id"], r["citation_key"]) in manual
            if manual_only is True and not is_manual:
                continue
            if manual_only is False and is_manual:
                continue
            result.append((i, r))
        return result

    metrics = {}
    for part in ("valid", "test"):
        for name, filter_manual in [
            ("all", None),
            ("manual", True),
            ("automatic", False),
        ]:
            sample = group(part, filter_manual)
            metrics[f"{part}_{name}"] = {
                "rows": len(sample),
                "labeled_matches": sum(
                    bool(r.get("groundtruth_arxiv_id")) for _, r in sample
                ),
                "baseline_mrr_at_5": mrr(sample, baseline_ranked),
                "model_mrr_at_5": mrr(sample, ranked),
            }

    primary_test = (
        metrics["test_manual"]
        if metrics["test_manual"]["rows"] > 0
        else metrics["test_all"]
    )
    primary_valid = (
        metrics["valid_manual"]
        if metrics["valid_manual"]["rows"] > 0
        else metrics["valid_all"]
    )

    out_dir = args.output / "matching" / "model_ranked_predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.parquet"):
        old.unlink()
    shard_size = 50000
    total = max(1, math.ceil(len(pred_rows) / shard_size))
    for shard, start in enumerate(range(0, len(pred_rows), shard_size)):
        batch = []
        for i in range(start, min(start + shard_size, len(pred_rows))):
            row = pred_rows[i]
            ids = ranked[i][:5] + [None] * max(0, 5 - len(ranked[i]))
            batch.append(
                {
                    "arxiv_id": row["arxiv_id"],
                    "partition": row["partition"],
                    "citation_key": row["citation_key"],
                    "groundtruth_arxiv_id": row.get("groundtruth_arxiv_id"),
                    **{f"cand_{j+1}": ids[j] for j in range(5)},
                }
            )
        pq.write_table(
            pa.Table.from_pylist(batch, schema=SCHEMA),
            out_dir / f"train-{shard:05d}-of-{total:05d}.parquet",
            compression="zstd",
        )

    report = {
        "model": "numpy logistic candidate ranker",
        "features": list(FEATURES),
        "training_pairs": len(y),
        "training_positive_pairs": int(y.sum()),
        "training_rows": by_partition["train"],
        "manual_training_rows": manual_counts["train"],
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "l2": args.l2,
        "manual_labels_total": len(manual),
        "manual_publications": len({p for p, _ in manual}),
        "partition_rows": dict(by_partition),
        "metrics": metrics,
        "primary_manual_evaluation": {
            "valid_mrr_at_5": primary_valid["model_mrr_at_5"],
            "test_mrr_at_5": primary_test["model_mrr_at_5"],
            "test_rows": primary_test["rows"],
            "test_matched_labels": primary_test["labeled_matches"],
            "test_no_match_labels": primary_test["rows"]
            - primary_test["labeled_matches"],
            "baseline_test_mrr_at_5": primary_test["baseline_mrr_at_5"],
        },
        "test_all_evaluation": {
            "valid_mrr_at_5": metrics["valid_all"]["model_mrr_at_5"],
            "test_mrr_at_5": metrics["test_all"]["model_mrr_at_5"],
            "test_rows": metrics["test_all"]["rows"],
            "baseline_test_mrr_at_5": metrics["test_all"]["baseline_mrr_at_5"],
        },
    }
    (args.output / "matching" / "mrr_evaluation.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    model = {
        "model": report["model"],
        "features": list(FEATURES),
        "mean": mu.tolist(),
        "scale": sd.tolist(),
        "weights": w.tolist(),
        "intercept": bias,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "l2": args.l2,
    }
    (args.output / "matching" / "candidate_ranker_model.json").write_text(
        json.dumps(model, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Model-ranked prediction shards: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
