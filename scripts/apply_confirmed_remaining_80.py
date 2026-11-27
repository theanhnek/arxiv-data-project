import csv, json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

remaining=Path('data/matching/manual_label_review_remaining_80.csv')
with remaining.open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f)); remaining_fields=list(rows[0])
if len(rows)!=80: raise SystemExit(f'Expected 80 rows; got {len(rows)}')
per={}
for r in rows:
    p=r['arxiv_id']; per[p]=per.get(p,0)+1
if len(per)!=4 or any(n!=20 for n in per.values()): raise SystemExit(f'Expected four papers x20: {per}')
for r in rows:
    label=r['proposed_label'].strip()
    if label!='NONE' and not label: raise SystemExit(f'Missing proposal for {r["citation_key"]}')
    r['manual_groundtruth_arxiv_id']=label
    r['review_status']='CONFIRMED_BY_USER'
with remaining.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=remaining_fields); w.writeheader(); w.writerows(rows)

main_csv=Path('data/matching/manual_label_review.csv')
with main_csv.open(encoding='utf-8-sig',newline='') as f:
    main_rows=list(csv.DictReader(f)); main_fields=list(main_rows[0])
label_map={(r['arxiv_id'],r['citation_key']):r['manual_groundtruth_arxiv_id'] for r in rows}
for r in main_rows:
    k=(r['arxiv_id'],r['citation_key'])
    if k in label_map:
        r['manual_groundtruth_arxiv_id']=label_map[k]
        r['review_status']='CONFIRMED_BY_USER'
with main_csv.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=main_fields); w.writeheader(); w.writerows(main_rows)

labels=[]
for r in main_rows:
    if r['review_status']!='CONFIRMED_BY_USER': continue
    raw=r['manual_groundtruth_arxiv_id'].strip()
    labels.append({'arxiv_id':r['arxiv_id'],'citation_key':r['citation_key'],
      'groundtruth_arxiv_id':None if raw.upper()=='NONE' else raw,
      'manual_label':'NONE' if raw.upper()=='NONE' else raw,
      'label_source':'user_confirmed','review_status':'confirmed'})
if len(labels)!=100: raise SystemExit(f'Expected 100 confirmed labels; got {len(labels)}')
Path('data/matching/manual_labels.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in labels),encoding='utf-8')

partition={'2510.07077':'test','2510.04978':'valid','2510.06265':'train','2510.08962':'train','2510.09170':'train'}
schema=pa.schema([('arxiv_id',pa.string()),('partition',pa.string()),('citation_key',pa.string()),('groundtruth_arxiv_id',pa.string()),('cand_1',pa.string()),('cand_2',pa.string()),('cand_3',pa.string()),('cand_4',pa.string()),('cand_5',pa.string())])
pred=[]
for r in rows:
    raw=r['manual_groundtruth_arxiv_id']
    item={'arxiv_id':r['arxiv_id'],'partition':partition[r['arxiv_id']],'citation_key':r['citation_key'],'groundtruth_arxiv_id':None if raw.upper()=='NONE' else raw}
    for i in range(1,6): item[f'cand_{i}']=r.get(f'candidate_{i}_arxiv_id') or None
    pred.append(item)
out=Path('data/export/parquet/data/predictions/manual-reviewed-remaining-80.parquet')
pq.write_table(pa.Table.from_pylist(pred,schema=schema),out,compression='zstd')

summary_path=Path('data/matching/manual_label_review_summary.json')
summary=json.loads(summary_path.read_text(encoding='utf-8'))
counts={}
for item in labels: counts[item['arxiv_id']]=counts.get(item['arxiv_id'],0)+1
summary.update({'user_confirmed_labels':len(labels),'confirmed_publications':len(counts),'confirmed_by_publication':counts,'manual_label_minimum':100,'remaining_to_minimum_100':0,'remaining_manual_publications':0,'status':'Manual labeling minimum met: five publications with 20 user-confirmed labels each. Supervised model training and MRR@5 evaluation remain incomplete.'})
summary_path.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
readme=Path('data/export/parquet/README.md')
text=readme.read_text(encoding='utf-8-sig')
text=text.replace('The predictions table contains automatic heuristic rows for 10% of eligible publications and 20 user-confirmed manual labels for one publication. Four more manually labeled publications are still required; supervised model training and MRR@5 evaluation are also incomplete. Do not treat this as the final Milestone 3 result.','The predictions table contains automatic heuristic rows for 10% of eligible publications and 100 user-confirmed manual labels across five publications. Supervised model training and MRR@5 evaluation are still incomplete. Do not treat this as the final Milestone 3 result.')
readme.write_text(text,encoding='utf-8',newline='')
print(json.dumps({'confirmed_labels':len(labels),'publications':counts,'added_prediction_rows':len(pred),'partitions':{p:partition[p] for p in counts},'manual_shard':str(out)},ensure_ascii=False))
