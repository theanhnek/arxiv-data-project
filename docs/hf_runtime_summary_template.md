# Group 14 Hugging Face and Pipeline Runtime Summary

Use this table to combine each member's own run artifacts. Copy values from each member's `data/metrics/benchmark_summary.json`; do not infer missing values. Record one row per member and note which measurements are unavailable.

To generate a CSV and Markdown summary after members provide their files, place them at `data/team_metrics/<Slug>/benchmark_summary.json` (`Kiet`, `Duong`, `Ha`, `Kien`, `Anh`) and run:

```powershell
python scripts/aggregate_member_metrics.py --metrics-dir data/team_metrics
```

Optional dataset URLs can be added without editing the script, for example:

```powershell
python scripts/aggregate_member_metrics.py --metrics-dir data/team_metrics --dataset-url Ha=https://huggingface.co/datasets/OWNER/REPO
```

The script writes `data/metrics/group_runtime_summary.csv` and `data/metrics/group_runtime_summary.md`. It leaves unavailable metrics blank and labels summed member durations as cumulative processing time rather than group wall time.

| Member | Assigned arXiv range | Run date | Commit | IDs assigned | Papers resolved | Versions downloaded | Extraction completed | Discovery (s) | Download (s) | Extraction (s) | References (s) | End-to-end wall time (s) | Peak RAM (MB) | Average RAM (MB) | Peak working disk (MB) | Final output disk (MB) | Final corpus disk (MB) | Dataset URL |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Kiệt | 2025-10 #14401–19400 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| Dương | 2025-10 #9401–14400 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| **Hà** | **2025-10 #4401–9400** |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| Kiên | 2025-09 #26047 to 2025-10 #4400 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| Anh | 2025-09 #11047–16046 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

## Where to get the measurements

From `benchmark_summary.json`:

- IDs assigned: `discovery.assigned_unique_ids`
- Papers resolved: `discovery.resolved_papers`
- Versions downloaded: `download_and_extraction.downloaded_versions`
- Extraction completed: `download_and_extraction.version_completion_rate` plus the success/no-TeX counts
- Step times: `performance.discovery_elapsed_seconds`, `download_elapsed_seconds`, `extraction_elapsed_seconds`, `references_elapsed_seconds`
- End-to-end wall time: `performance.end_to_end_wall_time_seconds`
- RAM/disk: `performance.resources.peak_ram_mb`, `avg_ram_mb`, `peak_working_disk_mb`, `final_output_disk_mb`, `final_corpus_disk_mb`

Use the member's actual assigned range and upload URL, then check the public dataset URL in a signed-out/incognito browser before recording completion.

## Aggregate notes

- Total successful members: {{COMPLETED_MEMBER_COUNT}} / 5
- Total assigned IDs: {{TOTAL_ASSIGNED_IDS}}
- Total resolved papers: {{TOTAL_RESOLVED_PAPERS}}
- Combined processing wall time: {{AGGREGATION_METHOD_AND_VALUE}}
- RAM/disk summary method: {{AGGREGATION_METHOD}}
- Missing or incomparable run artifacts: {{NOTES}}

Do not sum overlapping parallel wall-clock durations and describe the result as elapsed group wall time. Report member durations individually, and label any sum as cumulative processing time.
