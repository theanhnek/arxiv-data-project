"""Offline unit tests for Member 5's performance and corpus metrics module."""

import json
import time

from src.config import CrawlConfig
from src.metrics import ResourceMonitor, compute_pipeline_metrics


def test_resource_monitor_captures_ram_and_disk(tmp_path):
    corpus_dir = tmp_path / "corpus" / "2509-11047" / "v1" / "source"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "main.tex").write_bytes(b"\\documentclass{article}\n" * 100)

    with ResourceMonitor(tmp_path, sample_interval_seconds=0.05) as monitor:
        time.sleep(0.08)

    snap = monitor.snapshot
    assert snap is not None
    assert snap.samples_count >= 1
    assert snap.peak_ram_bytes > 0
    assert snap.avg_ram_bytes > 0
    assert snap.final_corpus_disk_bytes == 2400
    assert snap.peak_working_disk_bytes >= 2400


def test_compute_pipeline_metrics_without_references(tmp_path):
    config = CrawlConfig(output_dir=tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    (state_dir / "input_validation.json").write_text(
        json.dumps(
            {
                "ids": ["2509.11047", "2509.11048"],
                "invalid": [{"line": 3, "value": "bad-id"}],
                "duplicates": ["2509.11047"],
            }
        ),
        encoding="utf-8",
    )

    (state_dir / "discovery.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elapsed_seconds": 6.2,
                "summary": {"assigned_unique_ids": 2, "resolved": 2, "failed": 0},
                "papers": [
                    {
                        "arxiv_id": "2509.11047",
                        "status": "resolved",
                        "discovery_seconds": 3.1,
                        "versions": [
                            {"version": 1, "version_id": "2509.11047v1"},
                            {"version": 2, "version_id": "2509.11047v2"},
                        ],
                    },
                    {
                        "arxiv_id": "2509.11048",
                        "status": "resolved",
                        "discovery_seconds": 3.1,
                        "versions": [
                            {"version": 1, "version_id": "2509.11048v1"},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    (state_dir / "downloads.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elapsed_seconds": 9.5,
                "versions": [
                    {
                        "version_id": "2509.11047v1",
                        "arxiv_id": "2509.11047",
                        "version": 1,
                        "status": "downloaded",
                        "bytes_downloaded": 500000,
                        "duration_seconds": 3.0,
                    },
                    {
                        "version_id": "2509.11047v2",
                        "arxiv_id": "2509.11047",
                        "version": 2,
                        "status": "downloaded",
                        "bytes_downloaded": 600000,
                        "duration_seconds": 3.5,
                    },
                    {
                        "version_id": "2509.11048v1",
                        "arxiv_id": "2509.11048",
                        "version": 1,
                        "status": "downloaded",
                        "bytes_downloaded": 200000,
                        "duration_seconds": 3.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    (state_dir / "extractions.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elapsed_seconds": 0.4,
                "versions": [
                    {
                        "version_id": "2509.11047v1",
                        "arxiv_id": "2509.11047",
                        "version": 1,
                        "status": "success",
                        "input_bytes": 500000,
                        "archive_uncompressed_bytes": 800000,
                        "retained_bytes": 40000,
                        "files": ["main.tex", "refs.bib"],
                    },
                    {
                        "version_id": "2509.11047v2",
                        "arxiv_id": "2509.11047",
                        "version": 2,
                        "status": "success",
                        "input_bytes": 600000,
                        "archive_uncompressed_bytes": 900000,
                        "retained_bytes": 45000,
                        "files": ["main.tex", "refs.bib"],
                    },
                    {
                        "version_id": "2509.11048v1",
                        "arxiv_id": "2509.11048",
                        "version": 1,
                        "status": "no_tex_source",
                        "input_bytes": 200000,
                        "archive_uncompressed_bytes": 300000,
                        "retained_bytes": 0,
                        "files": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = compute_pipeline_metrics(config)

    assert report["discovery"]["assigned_unique_ids"] == 2
    assert report["discovery"]["resolved_papers"] == 2
    assert report["discovery"]["paper_discovery_success_rate"] == 1.0
    assert report["discovery"]["discovered_versions"] == 3
    assert report["discovery"]["multi_version_papers"] == 1

    assert report["download_and_extraction"]["downloaded_versions"] == 3
    assert report["download_and_extraction"]["extracted_tex_versions"] == 2
    assert report["download_and_extraction"]["no_tex_source_versions"] == 1
    assert report["download_and_extraction"]["version_completion_rate"] == 1.0
    assert report["download_and_extraction"]["full_paper_completion_count"] == 2
    assert report["download_and_extraction"]["total_tex_files"] == 2
    assert report["download_and_extraction"]["total_bib_files"] == 2

    # Total uncompressed = 2,000,000; retained = 85,000 -> removed = 1,915,000 (95.75% savings)
    assert report["sizes_bytes"]["total_uncompressed_archive_bytes_before_filtering"] == 2000000
    assert report["sizes_bytes"]["total_retained_tex_bib_bytes_after_filtering"] == 85000
    assert report["sizes_bytes"]["total_non_tex_bytes_removed"] == 1915000
    assert report["sizes_bytes"]["figure_removal_savings_ratio"] == 0.9575

    # Per-paper average uncompressed: (1,700,000 + 300,000) / 2 = 1,000,000
    assert report["sizes_bytes"]["avg_uncompressed_bytes_per_paper_before_filtering"] == 1000000.0
    assert report["sizes_bytes"]["avg_retained_bytes_per_paper_after_filtering"] == 42500.0

    # References not crawled yet -> available is False, no errors raised
    assert report["references"]["available"] is False
    assert report["references"]["queried_papers"] == 0

    assert (tmp_path / "metrics" / "benchmark_summary.json").is_file()
    assert (tmp_path / "metrics" / "per_paper_metrics.json").is_file()


def test_compute_pipeline_metrics_with_references(tmp_path):
    config = CrawlConfig(output_dir=tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    (state_dir / "discovery.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elapsed_seconds": 3.0,
                "summary": {"assigned_unique_ids": 1, "resolved": 1, "failed": 0},
                "papers": [
                    {
                        "arxiv_id": "2509.11047",
                        "status": "resolved",
                        "discovery_seconds": 3.0,
                        "versions": [{"version": 1, "version_id": "2509.11047v1"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    (state_dir / "references.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "elapsed_seconds": 1.5,
                "papers": [
                    {
                        "arxiv_id": "2509.11047",
                        "status": "success",
                        "total_references_count": 2,
                        "arxiv_references_count": 1,
                        "duration_seconds": 1.5,
                        "normalized_references": [
                            {
                                "arxiv_id": "2509.11047",
                                "ref_arxiv_id": "1706.03762",
                                "ref_title": "Attention Is All You Need",
                                "ref_authors": ["Ashish Vaswani"],
                                "ref_submission_date": "2017-06-12",
                                "ref_s2_id": "s2-1",
                            },
                            {
                                "arxiv_id": "2509.11047",
                                "ref_arxiv_id": None,
                                "ref_title": "Book Reference",
                                "ref_authors": ["Author B"],
                                "ref_submission_date": None,
                                "ref_s2_id": "s2-2",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = compute_pipeline_metrics(config)
    assert report["references"]["available"] is True
    assert report["references"]["queried_papers"] == 1
    assert report["references"]["reference_scrape_success_rate"] == 1.0
    assert report["references"]["total_references_crawled"] == 2
    assert report["references"]["total_arxiv_references_crawled"] == 1
    assert report["references"]["field_coverage_rates"]["arxiv_id"] == 0.5
    assert report["references"]["field_coverage_rates"]["submission_date"] == 0.5
    assert report["references"]["field_coverage_rates"]["s2_paper_id"] == 1.0
