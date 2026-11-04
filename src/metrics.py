"""Member 5: auditable end-to-end performance measurements and corpus statistics.

Implements:
- ``ResourceMonitor``: background sampler using ``psutil`` to measure main + child
  process RSS memory (average and peak RAM) and working directory disk usage
  (peak working disk including temporary archives and final corpus size).
- ``compute_pipeline_metrics(config, *, monitor_result=None)``: aggregates state
  snapshots from Discovery, Downloader, Extractor, and (when available) References
  into auditable summary and per-paper metrics JSON files for the Jupyter Book report.
- CLI entry point ``python -m src.metrics --output data`` to compute or refresh
  ``data/metrics/benchmark_summary.json`` and ``data/metrics/per_paper_metrics.json``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

from .config import CrawlConfig
from .discovery import write_json
from .downloader import safe_paper_dirname


def _directory_size_bytes(path: Path) -> int:
    """Return total size in bytes of all regular files under ``path``."""
    if not path.exists():
        return 0
    total = 0
    try:
        for root, _, files in os.walk(path):
            root_path = Path(root)
            for name in files:
                file_path = root_path / name
                try:
                    if not file_path.is_symlink():
                        total += file_path.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _process_tree_rss_bytes(pid: Optional[int] = None) -> int:
    """Return total RSS memory in bytes of the process and all its children."""
    try:
        proc = psutil.Process(pid or os.getpid())
        total_rss = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total_rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total_rss
    except (psutil.Error, OSError):
        return 0


def _git_commit_hash() -> Optional[str]:
    """Return current short git commit hash if running inside a git repository."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip() or None
    except OSError:
        pass
    return None


def collect_environment_info(output_dir: Path) -> Dict[str, Any]:
    """Capture auditable hardware and software environment metadata."""
    vm = psutil.virtual_memory()
    disk_free = None
    disk_total = None
    try:
        target = output_dir if output_dir.exists() else output_dir.parent
        usage = shutil.disk_usage(target)
        disk_total = usage.total
        disk_free = usage.free
    except OSError:
        pass

    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "system_total_ram_bytes": vm.total,
        "system_total_ram_gb": round(vm.total / (1024 ** 3), 2),
        "filesystem_total_bytes": disk_total,
        "filesystem_free_bytes": disk_free,
        "git_commit": _git_commit_hash(),
        "is_colab": "COLAB_RELEASE_TAG" in os.environ or "COLAB_GPU" in os.environ,
    }


@dataclass
class ResourceSnapshot:
    """Aggregated resource measurements from a monitored execution."""

    started_at: str
    finished_at: str
    wall_time_seconds: float
    sample_interval_seconds: float
    samples_count: int
    peak_ram_bytes: int
    avg_ram_bytes: float
    peak_working_disk_bytes: int
    final_output_disk_bytes: int
    final_corpus_disk_bytes: int
    final_tmp_disk_bytes: int
    final_state_disk_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_time_seconds": round(self.wall_time_seconds, 4),
            "sample_interval_seconds": self.sample_interval_seconds,
            "samples_count": self.samples_count,
            "peak_ram_bytes": self.peak_ram_bytes,
            "peak_ram_mb": round(self.peak_ram_bytes / (1024 * 1024), 3),
            "avg_ram_bytes": round(self.avg_ram_bytes, 2),
            "avg_ram_mb": round(self.avg_ram_bytes / (1024 * 1024), 3),
            "peak_working_disk_bytes": self.peak_working_disk_bytes,
            "peak_working_disk_mb": round(self.peak_working_disk_bytes / (1024 * 1024), 3),
            "final_output_disk_bytes": self.final_output_disk_bytes,
            "final_output_disk_mb": round(self.final_output_disk_bytes / (1024 * 1024), 3),
            "final_corpus_disk_bytes": self.final_corpus_disk_bytes,
            "final_corpus_disk_mb": round(self.final_corpus_disk_bytes / (1024 * 1024), 3),
            "final_tmp_disk_bytes": self.final_tmp_disk_bytes,
            "final_state_disk_bytes": self.final_state_disk_bytes,
        }


class ResourceMonitor:
    """Periodic background sampler for process tree RAM and working directory disk usage."""

    def __init__(self, output_dir: Path, sample_interval_seconds: float = 0.5):
        self.output_dir = Path(output_dir)
        self.sample_interval_seconds = max(0.05, float(sample_interval_seconds))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ram_samples: List[int] = []
        self._peak_disk_bytes: int = 0
        self._started_mono: float = 0.0
        self._started_at: str = ""
        self.snapshot: Optional[ResourceSnapshot] = None

    def _sample_once(self) -> None:
        rss = _process_tree_rss_bytes()
        if rss > 0:
            self._ram_samples.append(rss)
        disk_now = _directory_size_bytes(self.output_dir)
        if disk_now > self._peak_disk_bytes:
            self._peak_disk_bytes = disk_now

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.sample_interval_seconds)

    def start(self) -> "ResourceMonitor":
        self._stop_event.clear()
        self._ram_samples.clear()
        self._peak_disk_bytes = 0
        self._started_mono = time.monotonic()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._sample_once()
        self._thread = threading.Thread(
            target=self._run_loop, name="ArxivResourceMonitor", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> ResourceSnapshot:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._sample_once()
        wall_time = max(0.0, time.monotonic() - self._started_mono)
        finished_at = datetime.now(timezone.utc).isoformat()

        final_output = _directory_size_bytes(self.output_dir)
        final_corpus = _directory_size_bytes(self.output_dir / "corpus")
        final_tmp = _directory_size_bytes(self.output_dir / "tmp")
        final_state = _directory_size_bytes(self.output_dir / "state")
        peak_disk = max(self._peak_disk_bytes, final_output)

        peak_ram = max(self._ram_samples) if self._ram_samples else 0
        avg_ram = (
            sum(self._ram_samples) / len(self._ram_samples)
            if self._ram_samples
            else 0.0
        )

        self.snapshot = ResourceSnapshot(
            started_at=self._started_at,
            finished_at=finished_at,
            wall_time_seconds=wall_time,
            sample_interval_seconds=self.sample_interval_seconds,
            samples_count=len(self._ram_samples),
            peak_ram_bytes=peak_ram,
            avg_ram_bytes=avg_ram,
            peak_working_disk_bytes=peak_disk,
            final_output_disk_bytes=final_output,
            final_corpus_disk_bytes=final_corpus,
            final_tmp_disk_bytes=final_tmp,
            final_state_disk_bytes=final_state,
        )
        return self.snapshot

    def __enter__(self) -> "ResourceMonitor":
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def compute_pipeline_metrics(
    config: CrawlConfig,
    *,
    monitor_snapshot: Optional[ResourceSnapshot] = None,
) -> Dict[str, Any]:
    """Aggregate all pipeline states and resource measurements into an auditable report.

    Works seamlessly even if Step 4 (``state/references.json``) has not been executed yet,
    marking ``references.available = False`` until reference data is present.
    """
    state_dir = config.output_dir / "state"
    metrics_dir = config.output_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    input_val = _safe_load_json(state_dir / "input_validation.json") or {}
    discovery = _safe_load_json(state_dir / "discovery.json") or {}
    downloads = _safe_load_json(state_dir / "downloads.json") or {}
    extractions = _safe_load_json(state_dir / "extractions.json") or {}
    references = _safe_load_json(state_dir / "references.json")

    # 1. Input & Discovery metrics
    valid_ids = input_val.get("ids") or []
    invalid_inputs = input_val.get("invalid") or []
    duplicate_inputs = input_val.get("duplicates") or []

    discovered_papers = discovery.get("papers") or []
    assigned_unique_ids = (
        discovery.get("summary", {}).get("assigned_unique_ids")
        or len(valid_ids)
        or len(discovered_papers)
    )
    resolved_papers = [p for p in discovered_papers if p.get("status") == "resolved"]
    failed_papers = [p for p in discovered_papers if p.get("status") != "resolved"]

    total_discovered_versions = sum(len(p.get("versions", [])) for p in resolved_papers)
    multi_version_papers = sum(1 for p in resolved_papers if len(p.get("versions", [])) > 1)
    discovery_elapsed = float(discovery.get("elapsed_seconds") or 0.0)

    # 2. Download metrics indexed by version_id and arxiv_id
    dl_versions = downloads.get("versions") or []
    dl_by_vid: Dict[str, Dict[str, Any]] = {
        v["version_id"]: v for v in dl_versions if "version_id" in v
    }
    dl_downloaded = [v for v in dl_versions if v.get("status") == "downloaded"]
    dl_not_found = [v for v in dl_versions if v.get("status") == "not_found"]
    dl_invalid = [v for v in dl_versions if v.get("status") == "invalid_response"]
    dl_failed = [v for v in dl_versions if v.get("status") == "failed"]
    download_elapsed = float(downloads.get("elapsed_seconds") or 0.0)
    total_downloaded_bytes = sum(int(v.get("bytes_downloaded") or 0) for v in dl_versions)

    # 3. Extraction metrics indexed by version_id
    ext_versions = extractions.get("versions") or []
    ext_by_vid: Dict[str, Dict[str, Any]] = {
        v["version_id"]: v for v in ext_versions if "version_id" in v
    }
    ext_success = [v for v in ext_versions if v.get("status") == "success"]
    ext_no_tex = [v for v in ext_versions if v.get("status") == "no_tex_source"]
    ext_skipped = [v for v in ext_versions if v.get("status") == "skipped_not_downloaded"]
    ext_failed = [
        v
        for v in ext_versions
        if v.get("status") not in ("success", "no_tex_source", "skipped_not_downloaded")
    ]
    extraction_elapsed = float(extractions.get("elapsed_seconds") or 0.0)

    total_input_bytes = sum(int(v.get("input_bytes") or 0) for v in ext_versions)
    total_uncompressed_bytes = sum(
        int(v.get("archive_uncompressed_bytes") or 0) for v in ext_versions
    )
    total_retained_bytes = sum(int(v.get("retained_bytes") or 0) for v in ext_versions)
    total_extracted_files = sum(len(v.get("files") or []) for v in ext_versions)
    total_tex_files = sum(
        sum(1 for f in (v.get("files") or []) if str(f).lower().endswith(".tex"))
        for v in ext_versions
    )
    total_bib_files = sum(
        sum(1 for f in (v.get("files") or []) if str(f).lower().endswith(".bib"))
        for v in ext_versions
    )

    # 4. References metrics (optional if Member 4 hasn't run yet)
    ref_by_pid: Dict[str, Dict[str, Any]] = {}
    references_available = False
    references_elapsed = 0.0
    if references and isinstance(references.get("papers"), list):
        references_available = True
        references_elapsed = float(references.get("elapsed_seconds") or 0.0)
        for p in references["papers"]:
            if "arxiv_id" in p:
                ref_by_pid[p["arxiv_id"]] = p
    else:
        # Check if individual state/references/<safe_id>.json files exist
        ref_state_dir = state_dir / "references"
        if ref_state_dir.is_dir():
            for json_file in sorted(ref_state_dir.glob("*.json")):
                loaded = _safe_load_json(json_file)
                if loaded and "arxiv_id" in loaded:
                    ref_by_pid[loaded["arxiv_id"]] = loaded
            if ref_by_pid:
                references_available = True

    ref_records = list(ref_by_pid.values())
    ref_queried_count = len(ref_records)
    ref_success_count = sum(1 for r in ref_records if r.get("status") == "success")
    ref_empty_count = sum(1 for r in ref_records if r.get("status") == "empty_references")
    ref_not_found_count = sum(1 for r in ref_records if r.get("status") == "not_found")
    ref_failed_count = sum(1 for r in ref_records if r.get("status") == "failed")
    ref_api_ok_count = ref_success_count + ref_empty_count

    total_refs_count = 0
    total_arxiv_refs_count = 0
    refs_with_date = 0
    refs_with_s2_id = 0
    refs_with_title = 0
    refs_with_authors = 0

    for r in ref_records:
        norm_list = r.get("normalized_references") or []
        raw_list = r.get("raw_references") or []
        count_here = int(r.get("total_references_count") or len(norm_list) or len(raw_list))
        arxiv_here = int(
            r.get("arxiv_references_count")
            or sum(1 for item in norm_list if item.get("ref_arxiv_id"))
        )
        total_refs_count += count_here
        total_arxiv_refs_count += arxiv_here
        for item in norm_list:
            if item.get("ref_submission_date"):
                refs_with_date += 1
            if item.get("ref_s2_id"):
                refs_with_s2_id += 1
            if item.get("ref_title"):
                refs_with_title += 1
            if item.get("ref_authors"):
                refs_with_authors += 1

    # 5. Per-paper synthesis & full-paper completion calculation
    per_paper_records: List[Dict[str, Any]] = []
    full_paper_completed_count = 0
    paper_compressed_Totals: List[int] = []
    paper_uncompressed_Totals: List[int] = []
    paper_retained_Totals: List[int] = []
    paper_durations: List[float] = []

    for paper in discovered_papers:
        arxiv_id = paper.get("arxiv_id", "")
        p_status = paper.get("status", "unknown")
        disc_sec = float(paper.get("discovery_seconds") or 0.0)
        versions = paper.get("versions") or []
        v_count = len(versions)

        p_dl_bytes = 0
        p_uncompressed_bytes = 0
        p_retained_bytes = 0
        p_dl_sec = 0.0
        p_tex_files = 0
        p_bib_files = 0
        v_completed = 0
        v_with_tex = 0

        for v_item in versions:
            vid = v_item.get("version_id", "")
            dl_entry = dl_by_vid.get(vid, {})
            ext_entry = ext_by_vid.get(vid, {})

            p_dl_bytes += int(dl_entry.get("bytes_downloaded") or 0)
            p_dl_sec += float(dl_entry.get("duration_seconds") or 0.0)
            p_uncompressed_bytes += int(ext_entry.get("archive_uncompressed_bytes") or 0)
            p_retained_bytes += int(ext_entry.get("retained_bytes") or 0)

            files_here = ext_entry.get("files") or []
            p_tex_files += sum(1 for f in files_here if str(f).lower().endswith(".tex"))
            p_bib_files += sum(1 for f in files_here if str(f).lower().endswith(".bib"))

            ext_st = ext_entry.get("status")
            if ext_st in ("success", "no_tex_source"):
                v_completed += 1
            if ext_st == "success":
                v_with_tex += 1

        ref_entry = ref_by_pid.get(arxiv_id, {})
        ref_sec = float(ref_entry.get("duration_seconds") or 0.0)
        p_total_sec = disc_sec + p_dl_sec + ref_sec

        is_fully_completed = p_status == "resolved" and v_count > 0 and v_completed == v_count
        if is_fully_completed:
            full_paper_completed_count += 1

        if p_status == "resolved":
            paper_compressed_Totals.append(p_dl_bytes)
            paper_uncompressed_Totals.append(p_uncompressed_bytes)
            paper_retained_Totals.append(p_retained_bytes)
            paper_durations.append(p_total_sec)

        per_paper_records.append(
            {
                "arxiv_id": arxiv_id,
                "safe_id": safe_paper_dirname(arxiv_id) if arxiv_id else "",
                "discovery_status": p_status,
                "versions_discovered": v_count,
                "versions_completed": v_completed,
                "versions_with_tex": v_with_tex,
                "full_paper_completed": is_fully_completed,
                "compressed_bytes": p_dl_bytes,
                "archive_uncompressed_bytes": p_uncompressed_bytes,
                "retained_tex_bib_bytes": p_retained_bytes,
                "bytes_removed_by_filtering": max(0, p_uncompressed_bytes - p_retained_bytes),
                "tex_files_count": p_tex_files,
                "bib_files_count": p_bib_files,
                "references_status": ref_entry.get("status", "not_crawled"),
                "total_references_count": int(ref_entry.get("total_references_count") or 0),
                "arxiv_references_count": int(ref_entry.get("arxiv_references_count") or 0),
                "discovery_seconds": round(disc_sec, 4),
                "download_seconds": round(p_dl_sec, 4),
                "references_seconds": round(ref_sec, 4),
                "total_paper_seconds": round(p_total_sec, 4),
            }
        )

    # 6. Size averages (per version and per resolved paper)
    processed_versions_count = len(ext_success) + len(ext_no_tex)
    size_denom_versions = max(1, processed_versions_count)
    size_denom_papers = max(1, len(resolved_papers))

    bytes_removed_total = max(0, total_uncompressed_bytes - total_retained_bytes)
    savings_ratio = (
        round(bytes_removed_total / total_uncompressed_bytes, 6)
        if total_uncompressed_bytes > 0
        else 0.0
    )

    # 7. Runtime & Memory / Disk footprint
    stage_wall_time = (
        discovery_elapsed + download_elapsed + extraction_elapsed + references_elapsed
    )
    if monitor_snapshot is None:
        # Take a point-in-time measurement of current RAM and output directory disk usage
        now_iso = datetime.now(timezone.utc).isoformat()
        rss_now = _process_tree_rss_bytes()
        out_disk = _directory_size_bytes(config.output_dir)
        corpus_disk = _directory_size_bytes(config.output_dir / "corpus")
        tmp_disk = _directory_size_bytes(config.output_dir / "tmp")
        state_disk = _directory_size_bytes(config.output_dir / "state")
        monitor_snapshot = ResourceSnapshot(
            started_at=now_iso,
            finished_at=now_iso,
            wall_time_seconds=stage_wall_time,
            sample_interval_seconds=config.metrics_sample_interval_seconds,
            samples_count=1,
            peak_ram_bytes=rss_now,
            avg_ram_bytes=float(rss_now),
            peak_working_disk_bytes=out_disk,
            final_output_disk_bytes=out_disk,
            final_corpus_disk_bytes=corpus_disk,
            final_tmp_disk_bytes=tmp_disk,
            final_state_disk_bytes=state_disk,
        )

    effective_wall_time = max(monitor_snapshot.wall_time_seconds, stage_wall_time)

    summary_report: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": collect_environment_info(config.output_dir),
        "discovery": {
            "assigned_unique_ids": assigned_unique_ids,
            "invalid_inputs_count": len(invalid_inputs),
            "duplicate_inputs_count": len(duplicate_inputs),
            "resolved_papers": len(resolved_papers),
            "failed_papers": len(failed_papers),
            "paper_discovery_success_rate": _rate(len(resolved_papers), assigned_unique_ids),
            "discovered_versions": total_discovered_versions,
            "multi_version_papers": multi_version_papers,
            "avg_versions_per_resolved_paper": round(
                total_discovered_versions / size_denom_papers, 4
            )
            if resolved_papers
            else 0.0,
            "elapsed_seconds": round(discovery_elapsed, 4),
        },
        "download_and_extraction": {
            "expected_versions": total_discovered_versions,
            "downloaded_versions": len(dl_downloaded),
            "download_not_found": len(dl_not_found),
            "download_invalid_response": len(dl_invalid),
            "download_failed": len(dl_failed),
            "version_download_success_rate": _rate(
                len(dl_downloaded), total_discovered_versions
            ),
            "extracted_tex_versions": len(ext_success),
            "no_tex_source_versions": len(ext_no_tex),
            "skipped_not_downloaded_versions": len(ext_skipped),
            "extraction_failed_versions": len(ext_failed),
            "version_completion_rate": _rate(
                len(ext_success) + len(ext_no_tex), total_discovered_versions
            ),
            "tex_source_coverage_rate": _rate(len(ext_success), total_discovered_versions),
            "full_paper_completion_count": full_paper_completed_count,
            "full_paper_completion_rate": _rate(
                full_paper_completed_count, assigned_unique_ids
            ),
            "total_extracted_files": total_extracted_files,
            "total_tex_files": total_tex_files,
            "total_bib_files": total_bib_files,
        },
        "sizes_bytes": {
            "total_compressed_download_bytes": total_downloaded_bytes or total_input_bytes,
            "total_uncompressed_archive_bytes_before_filtering": total_uncompressed_bytes,
            "total_retained_tex_bib_bytes_after_filtering": total_retained_bytes,
            "total_non_tex_bytes_removed": bytes_removed_total,
            "figure_removal_savings_ratio": savings_ratio,
            "avg_compressed_bytes_per_version": round(
                (total_downloaded_bytes or total_input_bytes) / size_denom_versions, 2
            )
            if processed_versions_count
            else 0.0,
            "avg_uncompressed_bytes_per_version_before_filtering": round(
                total_uncompressed_bytes / size_denom_versions, 2
            )
            if processed_versions_count
            else 0.0,
            "avg_retained_bytes_per_version_after_filtering": round(
                total_retained_bytes / size_denom_versions, 2
            )
            if processed_versions_count
            else 0.0,
            "avg_compressed_bytes_per_paper": round(
                sum(paper_compressed_Totals) / size_denom_papers, 2
            )
            if resolved_papers
            else 0.0,
            "avg_uncompressed_bytes_per_paper_before_filtering": round(
                sum(paper_uncompressed_Totals) / size_denom_papers, 2
            )
            if resolved_papers
            else 0.0,
            "avg_retained_bytes_per_paper_after_filtering": round(
                sum(paper_retained_Totals) / size_denom_papers, 2
            )
            if resolved_papers
            else 0.0,
        },
        "references": {
            "available": references_available,
            "granularity": "publication_level",
            "queried_papers": ref_queried_count,
            "successful_api_queries": ref_api_ok_count,
            "papers_with_references": ref_success_count,
            "empty_references_papers": ref_empty_count,
            "not_found_papers": ref_not_found_count,
            "failed_queries": ref_failed_count,
            "reference_scrape_success_rate": _rate(ref_api_ok_count, ref_queried_count),
            "total_references_crawled": total_refs_count,
            "total_arxiv_references_crawled": total_arxiv_refs_count,
            "avg_references_per_queried_paper": round(
                total_refs_count / max(1, ref_api_ok_count), 4
            )
            if ref_api_ok_count
            else 0.0,
            "avg_arxiv_references_per_queried_paper": round(
                total_arxiv_refs_count / max(1, ref_api_ok_count), 4
            )
            if ref_api_ok_count
            else 0.0,
            "field_coverage_rates": {
                "arxiv_id": _rate(total_arxiv_refs_count, total_refs_count),
                "submission_date": _rate(refs_with_date, total_refs_count),
                "s2_paper_id": _rate(refs_with_s2_id, total_refs_count),
                "title": _rate(refs_with_title, total_refs_count),
                "authors": _rate(refs_with_authors, total_refs_count),
            },
            "elapsed_seconds": round(references_elapsed, 4),
        },
        "performance": {
            "discovery_elapsed_seconds": round(discovery_elapsed, 4),
            "download_elapsed_seconds": round(download_elapsed, 4),
            "extraction_elapsed_seconds": round(extraction_elapsed, 4),
            "references_elapsed_seconds": round(references_elapsed, 4),
            "sum_stage_elapsed_seconds": round(stage_wall_time, 4),
            "end_to_end_wall_time_seconds": round(effective_wall_time, 4),
            "avg_time_per_assigned_paper_seconds": round(
                effective_wall_time / max(1, assigned_unique_ids), 4
            )
            if assigned_unique_ids
            else 0.0,
            "avg_time_per_resolved_paper_seconds": round(
                effective_wall_time / size_denom_papers, 4
            )
            if resolved_papers
            else 0.0,
            "max_paper_processing_seconds": round(max(paper_durations), 4)
            if paper_durations
            else 0.0,
            "min_paper_processing_seconds": round(min(paper_durations), 4)
            if paper_durations
            else 0.0,
            "resources": monitor_snapshot.to_dict(),
        },
    }

    summary_path = metrics_dir / "benchmark_summary.json"
    per_paper_path = metrics_dir / "per_paper_metrics.json"
    write_json(summary_path, summary_report)
    write_json(
        per_paper_path,
        {
            "schema_version": 1,
            "generated_at": summary_report["generated_at"],
            "papers_count": len(per_paper_records),
            "papers": per_paper_records,
        },
    )
    return summary_report


def main() -> None:
    """CLI for Member 5: compute auditable metrics from data/state/*.json."""
    parser = argparse.ArgumentParser(
        description="Compute corpus statistics and performance metrics from pipeline state files."
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    args = parser.parse_args()

    config = CrawlConfig(output_dir=args.output)
    with ResourceMonitor(config.output_dir, config.metrics_sample_interval_seconds) as monitor:
        pass
    report = compute_pipeline_metrics(config, monitor_snapshot=monitor.snapshot)
    print("Metrics summary generated:")
    print(json.dumps(report["discovery"], indent=2))
    print(json.dumps(report["download_and_extraction"], indent=2))
    print(json.dumps(report["sizes_bytes"], indent=2))
    print(json.dumps(report["performance"], indent=2))
    print(f"Saved to: {args.output / 'metrics' / 'benchmark_summary.json'}")


if __name__ == "__main__":
    main()
