import csv, json
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

paper='2510.07077'
approved={
 'vaswani2017transformer':'1706.03762',
 'cer2018use':'1803.11175',
 'zhu2020robosuite':'2009.12293',
 'Adversarial':'2411.13587',
 'VLATest':'2409.12894',
 'yifan2025survey':'2507.01925',
 'sapkota2025survey':'2505.04769',
 'cheng2024vulnerabilities':'2409.13174',
 'epickitchens':'NONE',
 'guruprasad2024benchmark':'NONE',
 'lu2025ca':'NONE',
 'szot2024mllm':'NONE',
 'bastani2021safe':'NONE',
 'mambapolicy':'NONE',
 'ViLa':'NONE',
}
manual_csv=Path('data/matching/manual_label_review.csv')
with manual_csv.open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f)); fields=list(rows[0].keys())
for row in rows:
    if row['arxiv_id']==paper and row['citation_key'] in approved:
        label=approved[row['citation_key']]
        row['manual_groundtruth_arxiv_id']=label
        row['review_status']='CONFIRMED_BY_USER'
missing=set(approved)-{r['citation_key'] for r in rows if r['arxiv_id']==paper and r['review_status']=='CONFIRMED_BY_USER'}
if missing: raise SystemExit(f'Approved rows missing or not recorded: {sorted(missing)}')
with manual_csv.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

# Rebuild confirmed manual-label ledger from all user-confirmed rows in the review sheet.
labels=[]
for row in rows:
    if row['review_status']!='CONFIRMED_BY_USER': continue
    raw=row['manual_groundtruth_arxiv_id'].strip()
    labels.append({'arxiv_id':row['arxiv_id'],'citation_key':row['citation_key'],
                   'groundtruth_arxiv_id':None if raw.upper()=='NONE' else raw,
                   'manual_label':raw.upper() if raw.upper()=='NONE' else raw,
                   'label_source':'user_confirmed','review_status':'confirmed'})
labels_path=Path('data/matching/manual_labels.jsonl')
labels_path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in labels),encoding='utf-8')

schema=pa.schema([
 ('arxiv_id',pa.string()),('partition',pa.string()),('citation_key',pa.string()),
 ('groundtruth_arxiv_id',pa.string()),('cand_1',pa.string()),('cand_2',pa.string()),
 ('cand_3',pa.string()),('cand_4',pa.string()),('cand_5',pa.string())])
pred_rows=[]
for row in rows:
    if row['arxiv_id']!=paper or row['review_status']!='CONFIRMED_BY_USER': continue
    raw=row['manual_groundtruth_arxiv_id'].strip()
    pred={'arxiv_id':paper,'partition':'test','citation_key':row['citation_key'],
          'groundtruth_arxiv_id':None if raw.upper()=='NONE' else raw}
    for i in range(1,6): pred[f'cand_{i}']=row.get(f'candidate_{i}_arxiv_id') or None
    pred_rows.append(pred)
if len(pred_rows)!=20: raise SystemExit(f'Expected 20 confirmed predictions, got {len(pred_rows)}')
out=Path('data/export/parquet/data/predictions/manual-reviewed-2510.07077.parquet')
pq.write_table(pa.Table.from_pylist(pred_rows,schema=schema),out,compression='zstd')

summary_path=Path('data/matching/manual_label_review_summary.json')
summary=json.loads(summary_path.read_text(encoding='utf-8'))
per={}
for item in labels: per[item['arxiv_id']]=per.get(item['arxiv_id'],0)+1
summary.update({'user_confirmed_labels':len(labels),'confirmed_publications':len(per),
                'confirmed_by_publication':per,'manual_label_minimum':100,
                'remaining_to_minimum_100':max(0,100-len(labels)),
                'remaining_manual_publications':max(0,5-len(per)),
                'status':'Partial: one publication has 20 user-confirmed manual labels; four more publications with at least 20 labels each are required.'})
summary_path.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'confirmed_labels':len(labels),'new_prediction_rows':len(pred_rows),
                  'confirmed_publications':per,'remaining_labels':max(0,100-len(labels)),
                  'remaining_manual_publications':max(0,5-len(per)),
                  'predictions_shard':str(out)},ensure_ascii=False))
