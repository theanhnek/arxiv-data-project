from __future__ import annotations
import argparse, csv, hashlib, json, math, re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import pyarrow.parquet as pq

STOP={"a","an","and","are","as","at","be","by","for","from","in","into","of","on","or","the","to","with","via","using","based","toward","towards"}
TOKEN_RE=re.compile(r"[a-z0-9]+")
def toks(text): return {x for x in TOKEN_RE.findall((text or "").lower()) if x not in STOP and len(x)>1}
def dice(a,b): return 2*len(a&b)/(len(a)+len(b)) if a and b else 0.0
def score(entry, ref, rt, ra):
    return .85*dice(toks(entry.get('title')),rt)+.15*dice(toks(' '.join(entry.get('authors') or [])),ra)
def files(path): return sorted(path.glob('*.parquet'))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,default=Path('data')); ap.add_argument('--entries-per-paper',type=int,default=20); ap.add_argument('--threshold',type=float,default=.8); args=ap.parse_args()
    base=args.output/'export'/'parquet'/'data'
    auto=json.loads((args.output/'matching'/'automatic_predictions_summary.json').read_text(encoding='utf-8'))
    auto_ids=set(auto['automatic_publication_ids'])
    refs=defaultdict(dict)
    for f in files(base/'references'):
        for batch in pq.ParquetFile(f).iter_batches(batch_size=50000,columns=['arxiv_id','ref_arxiv_id','ref_title','ref_authors']):
            for r in batch.to_pylist():
                if not r.get('arxiv_id') or not r.get('ref_arxiv_id'): continue
                rid=r['ref_arxiv_id']; old=refs[r['arxiv_id']].get(rid)
                if old is None or len(r.get('ref_title') or '')>len(old.get('ref_title') or ''): refs[r['arxiv_id']][rid]=r
    ref_tokens={p:{rid:(toks(r.get('ref_title')),toks(' '.join(r.get('ref_authors') or []))) for rid,r in rr.items()} for p,rr in refs.items()}
    bibfiles=files(base/'bibtex'); counts=Counter()
    for f in bibfiles:
        for batch in pq.ParquetFile(f).iter_batches(batch_size=75000,columns=['arxiv_id','title','authors']):
            for e in batch.to_pylist():
                p=e.get('arxiv_id')
                if not p or p in auto_ids or p not in refs: continue
                counts[p]+=1
    eligible=[p for p,n in counts.items() if n>=args.entries_per_paper]
    eligible.sort(key=lambda p:(-len(refs[p]),-counts[p],hashlib.sha256(p.encode()).hexdigest()))
    selected=eligible[:5]
    if len(selected)<5: raise SystemExit(f'Only {len(selected)} eligible manual-review publications found; need 5')
    chosen={p:[] for p in selected}
    for f in bibfiles:
        for batch in pq.ParquetFile(f).iter_batches(batch_size=75000,columns=['arxiv_id','citation_key','title','authors','year']):
            for e in batch.to_pylist():
                p=e.get('arxiv_id')
                if p not in chosen or len(chosen[p])>=args.entries_per_paper: continue
                scored=[]
                for rid,r in refs[p].items():
                    s=score(e,r,*ref_tokens[p][rid]); scored.append((s,rid,r))
                scored.sort(key=lambda x:(-x[0],x[1]))
                chosen[p].append((e,scored[:5]))
    rows=[]
    for p in selected:
        chosen[p].sort(key=lambda x:(-x[1][0][0] if x[1] else 0, x[0].get('citation_key') or ''))
        for e,cands in chosen[p]:
            row={'arxiv_id':p,'citation_key':e.get('citation_key') or '', 'bibtex_title':e.get('title') or '', 'bibtex_authors':'; '.join(e.get('authors') or []), 'year':e.get('year'), 'suggested_groundtruth_arxiv_id':(cands[0][1] if cands and cands[0][0]>=args.threshold else ''), 'suggested_score':(round(cands[0][0],4) if cands else 0.0), 'manual_groundtruth_arxiv_id':'', 'review_status':'NEEDS_REVIEW'}
            for i in range(5):
                if i<len(cands):
                    s,rid,ref=cands[i]; row[f'candidate_{i+1}_arxiv_id']=rid; row[f'candidate_{i+1}_title']=ref.get('ref_title') or ''; row[f'candidate_{i+1}_score']=round(s,4)
                else:
                    row[f'candidate_{i+1}_arxiv_id']=''; row[f'candidate_{i+1}_title']=''; row[f'candidate_{i+1}_score']=''
            rows.append(row)
    out=args.output/'matching'/'manual_label_review.csv'; out.parent.mkdir(parents=True,exist_ok=True)
    fields=list(rows[0])
    with out.open('w',encoding='utf-8-sig',newline='') as h:
        w=csv.DictWriter(h,fieldnames=fields); w.writeheader(); w.writerows(rows)
    summary={'selected_publications':selected,'entries_per_publication':args.entries_per_paper,'rows':len(rows),'suggested_above_threshold':sum(bool(r['suggested_groundtruth_arxiv_id']) for r in rows),'threshold':args.threshold,'automatic_pool_excluded':True,'instruction':'Review every row; copy candidate ID to manual_groundtruth_arxiv_id only when verified; enter NONE if no matching arXiv reference; then set review_status=CONFIRMED.'}
    (out.parent/'manual_label_review_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False)); print(f'CSV: {out}')
if __name__=='__main__': main()

\n