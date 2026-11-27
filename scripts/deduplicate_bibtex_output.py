from __future__ import annotations
import json, os
from pathlib import Path
from collections import Counter
import pyarrow as pa
import pyarrow.parquet as pq

root = Path('data/export/parquet/data')
src = Path('data/export/bibtex.jsonl')
tmp = src.with_suffix('.jsonl.tmp')
schema = pa.schema([
    ('arxiv_id', pa.string()), ('citation_key', pa.string()), ('entry_type', pa.string()),
    ('title', pa.string()), ('authors', pa.list_(pa.string())), ('year', pa.int32()),
    ('doi', pa.string()), ('journal', pa.string()), ('url', pa.string()),
])
counts = Counter(); rows = 0; current_paper = None; entries = {}

def flush(handle):
    global rows
    for rec in entries.values():
        handle.write(json.dumps(rec, ensure_ascii=False) + '\n')
        rows += 1

with src.open('r', encoding='utf-8') as inp, tmp.open('w', encoding='utf-8') as out:
    for line in inp:
        rec = json.loads(line); paper = rec['arxiv_id']; key = rec['citation_key']
        if current_paper is not None and paper != current_paper:
            flush(out); entries = {}
        current_paper = paper
        if key not in entries:
            entries[key] = rec
        else:
            counts['duplicates_removed'] += 1
            target = entries[key]
            if len(rec.get('title') or '') > len(target.get('title') or ''):
                target['title'] = rec['title']
            for field in ('year', 'doi', 'journal', 'url'):
                if target.get(field) is None and rec.get(field) is not None:
                    target[field] = rec[field]
            authors = target.setdefault('authors', [])
            for author in rec.get('authors') or []:
                if author not in authors: authors.append(author)
    if entries: flush(out)
os.replace(tmp, src)

folder = root / 'bibtex'; folder.mkdir(parents=True, exist_ok=True)
for old in folder.glob('*.parquet'): old.unlink()
total = max(1, (rows + 49999) // 50000); batch = []; shard = 0
with src.open('r', encoding='utf-8') as inp:
    for line in inp:
        batch.append(json.loads(line))
        if len(batch) == 50000:
            pq.write_table(pa.Table.from_pylist(batch, schema=schema), folder / f'train-{shard:05d}-of-{total:05d}.parquet', compression='zstd')
            shard += 1; batch.clear()
if batch or shard == 0:
    pq.write_table(pa.Table.from_pylist(batch, schema=schema), folder / f'train-{shard:05d}-of-{total:05d}.parquet', compression='zstd')
print(json.dumps({'deduplicated_rows': rows, **counts, 'shards': total}))
