"""Safely replace the exported predictions table with trained model rankings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Optional, Sequence

import pyarrow.parquet as pq

EXPECTED_COLUMNS = [
    "arxiv_id",
    "partition",
    "citation_key",
    "groundtruth_arxiv_id",
    "cand_1",
    "cand_2",
    "cand_3",
    "cand_4",
    "cand_5",
]


def inspect(folder: Path) -> tuple[int, set[tuple[str, str]]]:
    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise SystemExit(f"No prediction shards found in {folder}")
    rows = 0
    keys: set[tuple[str, str]] = set()
    for path in files:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != EXPECTED_COLUMNS:
            raise SystemExit(
                f"Unexpected schema in {path}: {parquet.schema_arrow.names}"
            )
        for batch in parquet.iter_batches(
            batch_size=50_000, columns=["arxiv_id", "citation_key"]
        ):
            for paper, citation in zip(
                batch.column(0).to_pylist(), batch.column(1).to_pylist()
            ):
                key = (paper, citation)
                if key in keys:
                    raise SystemExit(
                        f"Duplicate prediction key in {folder}: {key}"
                    )
                keys.add(key)
                rows += 1
    return rows, keys


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--expected-rows",
        type=int,
        default=None,
        help="Optional exact row count to enforce; defaults to matching current predictions table",
    )
    args = parser.parse_args(argv)

    root = args.output
    target = root / "export" / "parquet" / "data" / "predictions"
    source = root / "matching" / "model_ranked_predictions"
    stage = root / "matching" / "predictions_promote_stage"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = root / "matching" / f"heuristic_predictions_backup_{ts}"

    if not source.is_dir() or not target.is_dir():
        raise SystemExit(
            f"Source model rankings ({source}) or exported predictions table ({target}) is missing"
        )
    if stage.exists():
        shutil.rmtree(stage)

    target_rows, target_keys = inspect(target)
    stage.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.parquet")):
        shutil.copy2(path, stage / path.name)

    rows, keys = inspect(stage)
    expected = (
        args.expected_rows if args.expected_rows is not None else target_rows
    )
    if rows != expected:
        shutil.rmtree(stage, ignore_errors=True)
        raise SystemExit(f"Expected {expected} prediction rows, got {rows}")
    if keys != target_keys:
        shutil.rmtree(stage, ignore_errors=True)
        raise SystemExit(
            "Key set mismatch between model_ranked_predictions and current predictions table"
        )

    # Keep the previous exported table as a recovery copy before promoting.
    target.rename(backup)
    try:
        stage.rename(target)
    except Exception:
        backup.rename(target)
        raise
    print(
        f"Promoted {rows:,} model-ranked predictions ({len(keys):,} unique keys) to {target}"
    )
    print(f"Previous heuristic table backed up at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
