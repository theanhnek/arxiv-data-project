import csv, json
from pathlib import Path
source=Path('data/matching/manual_label_review.csv')
target=Path('data/matching/manual_label_review_remaining_80.csv')
manual={'2510.07077'}
with source.open(encoding='utf-8-sig',newline='') as f:
    rows=[r for r in csv.DictReader(f) if r['arxiv_id'] not in manual]
if len(rows)!=80:
    raise SystemExit(f'Expected exactly 80 remaining entries, found {len(rows)}')
fields=['arxiv_id','bibtex_title','citation_key','proposed_label','proposal_score','proposal_type',
        'candidate_1_arxiv_id','candidate_1_score','candidate_1_title',
        'candidate_2_arxiv_id','candidate_2_score','candidate_2_title',
        'candidate_3_arxiv_id','candidate_3_score','candidate_3_title',
        'candidate_4_arxiv_id','candidate_4_score','candidate_4_title',
        'candidate_5_arxiv_id','candidate_5_score','candidate_5_title',
        'manual_groundtruth_arxiv_id','review_status']
with target.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
    for r in rows:
        proposal=r['suggested_groundtruth_arxiv_id'] or 'NONE'
        w.writerow({
          'arxiv_id':r['arxiv_id'],'bibtex_title':r['bibtex_title'],'citation_key':r['citation_key'],
          'proposed_label':proposal,'proposal_score':r['suggested_score'] or r['candidate_1_score'],
          'proposal_type':'title/author similarity' if proposal!='NONE' else 'NONE suggestion; inspect candidates',
          **{f'candidate_{i}_{k}':r.get(f'candidate_{i}_{k}','') for i in range(1,6) for k in ('arxiv_id','score','title')},
          'manual_groundtruth_arxiv_id':'','review_status':'NEEDS_REVIEW'})
counts={p:sum(r['arxiv_id']==p for r in rows) for p in sorted({r['arxiv_id'] for r in rows})}
suggested=sum(bool(r['suggested_groundtruth_arxiv_id']) for r in rows)
summary={'file':str(target),'rows':len(rows),'publications':counts,'proposed_arxiv_id_labels':suggested,'proposed_NONE':len(rows)-suggested,'manual_groundtruth_filled':0,'note':'These are recommendations only. Verify each suggested ID; use NONE only when none of the listed references is the correct arXiv-keyed match.'}
target.with_name('manual_label_review_remaining_80_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
