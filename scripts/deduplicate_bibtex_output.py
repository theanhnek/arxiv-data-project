from __future__ import annotations

from collections import Counter
import json
import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_milestone2_tables import deduplicate_paper_bibtex  # noqa: E402


def main() -> int:
    root = Path("data/export/parquet/data")
    src = Path("data/export/bibtex.jsonl")
    tmp = src.with_suffix(".jsonl.tmp")
    schema = pa.schema(
        [
            ("arxiv_id", pa.string()),
            ("citation_key", pa.string()),
            ("entry_type", pa.string()),
            ("title", pa.string()),
            ("authors", pa.list_(pa.string())),
            ("year", pa.int32()),
            ("doi", pa.string()),
            ("journal", pa.string()),
            ("url", pa.string()),
        ]
    )
    counts: Counter = Counter()
    rows = 0
    current_paper = None
    paper_raw_entries = []

    def flush(handle, paper_id: str, raw_list: list) -> None:
        nonlocal rows
        unified, aliases = deduplicate_paper_bibtex(paper_id, raw_list)
        counts["duplicates_removed"] += len(raw_list) - len(unified)
        counts["content_aliases_merged"] += len(aliases)
        for rec in unified:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rows += 1

    with src.open("r", encoding="utf-8") as inp, tmp.open(
        "w", encoding="utf-8"
    ) as out:
        for line in inp:
            rec = json.loads(line)
            paper = rec["arxiv_id"]
            if current_paper is not None and paper != current_paper:
                flush(out, current_paper, paper_raw_entries)
                paper_raw_entries = []
            current_paper = paper
            paper_raw_entries.append(rec)
        if current_paper is not None and paper_raw_entries:
            flush(out, current_paper, paper_raw_entries)
    os.replace(tmp, src)

    folder = root / "bibtex"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.parquet"):
        old.unlink()
    total = max(1, (rows + 49999) // 50000)
    batch = []
    shard = 0
    with src.open("r", encoding="utf-8") as inp:
        for line in inp:
            batch.append(json.loads(line))
            if len(batch) == 50000:
                pq.write_table(
                    pa.Table.from_pylist(batch, schema=schema),
                    folder / f"train-{shard:05d}-of-{total:05d}.parquet",
                    compression="zstd",
                )
                shard += 1
                batch.clear()
    if batch or shard == 0:
        pq.write_table(
            pa.Table.from_pylist(batch, schema=schema),
            folder / f"train-{shard:05d}-of-{total:05d}.parquet",
            compression="zstd",
        )
    print(json.dumps({"deduplicated_rows": rows, **counts, "shards": total}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
