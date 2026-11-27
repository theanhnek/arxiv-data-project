import csv, json
from pathlib import Path
p=Path('data/matching/manual_label_review.csv')
approved={
 ('2510.07077','jyamada2025graspmpc'):'2509.06201',
 ('2510.07077','he2017maskrcnn'):'1703.06870',
 ('2510.07077','kawaharazuka2024foundation'):'2402.05741',
 ('2510.07077','RevLA'):'2409.15250',
 ('2510.07077','ma2024survey'):'2405.14093',
}
with p.open(encoding='utf-8-sig',newline='') as f:
    rows=list(csv.DictReader(f)); fields=rows[0].keys()
found=set()
for row in rows:
    key=(row['arxiv_id'],row['citation_key'])
    if key in approved:
        if row['suggested_groundtruth_arxiv_id'] != approved[key]:
            raise SystemExit(f"Suggested ID mismatch for {key}: {row['suggested_groundtruth_arxiv_id']}")
        row['manual_groundtruth_arxiv_id']=approved[key]
        row['review_status']='CONFIRMED_BY_USER'
        found.add(key)
if found != set(approved): raise SystemExit(f'Missing approved rows: {set(approved)-found}')
with p.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
labels=[{'arxiv_id':paper,'citation_key':key,'groundtruth_arxiv_id':target,'label_source':'user_confirmed_suggestion','review_status':'confirmed'} for (paper,key),target in approved.items()]
out=Path('data/matching/manual_labels.jsonl')
out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in labels),encoding='utf-8')
summary_path=Path('data/matching/manual_label_review_summary.json')
summary=json.loads(summary_path.read_text(encoding='utf-8'))
summary['user_confirmed_labels']=len(labels)
summary['confirmed_publications']=1
summary['confirmed_by_publication']={'2510.07077':len(labels)}
summary['remaining_to_minimum_100']=95
summary['status']='Partial: five user-approved labels recorded; five publications with at least 20 labels each are still required.'
summary_path.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'recorded':len(labels),'publication':'2510.07077','remaining_labels_for_100':95,'manual_csv':str(p),'manual_jsonl':str(out)},ensure_ascii=False))
