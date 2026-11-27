"""Safely replace the exported predictions table with trained model rankings."""
from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("data")
TARGET = ROOT / "export/parquet/data/predictions"
SOURCE = ROOT / "matching/model_ranked_predictions"
STAGE = ROOT / "matching/predictions_promote_stage"
BACKUP = ROOT / "matching/heuristic_predictions_backup_20261127"
EXPECTED_ROWS = 615_993  # 615,893 automatic + 100 user-confirmed manual rows
EXPECTED_COLUMNS = ["arxiv_id", "partition", "citation_key", "groundtruth_arxiv_id", "cand_1", "cand_2", "cand_3", "cand_4", "cand_5"]


def inspect(folder: Path) -> tuple[int, set[tuple[str, str]]]:
    files = sorted(folder.glob("*.parquet"))
    if len(files) != 13:
        raise SystemExit(f"Expected 13 prediction shards in {folder}, found {len(files)}")
    rows = 0
    keys: set[tuple[str, str]] = set()
    for path in files:
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow.names != EXPECTED_COLUMNS:
            raise SystemExit(f"Unexpected schema in {path}: {parquet.schema_arrow.names}")
        for batch in parquet.iter_batches(batch_size=50_000, columns=["arxiv_id", "citation_key"]):
            for paper, citation in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
                key = (paper, citation)
                if key in keys:
                    raise SystemExit(f"Duplicate prediction key: {key}")
                keys.add(key)
                rows += 1
    return rows, keys


def main() -> None:
    if not SOURCE.is_dir() or not TARGET.is_dir():
        raise SystemExit("Source model rankings or exported predictions table is missing")
    if STAGE.exists() or BACKUP.exists():
        raise SystemExit(f"Refusing to overwrite existing stage/backup: {STAGE} / {BACKUP}")
    STAGE.mkdir(parents=True)
    for path in SOURCE.glob("*.parquet"):
        shutil.copy2(path, STAGE / path.name)
    rows, keys = inspect(STAGE)
    if rows != EXPECTED_ROWS:
        raise SystemExit(f"Expected {EXPECTED_ROWS} prediction rows, got {rows}")
    # Keep the previous exported table as a recovery copy before promoting.
    TARGET.rename(BACKUP)
    try:
        STAGE.rename(TARGET)
    except Exception:
        BACKUP.rename(TARGET)
        raise
    print(f"Promoted {rows:,} model-ranked predictions ({len(keys):,} unique keys) to {TARGET}")
    print(f"Previous heuristic table backed up at {BACKUP}")


if __name__ == "__main__":
    main()
