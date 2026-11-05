"""Member 4: collect and cache Semantic Scholar references by base arXiv ID.

Public entry points:

* :func:`fetch_references` - query one publication and return a :class:`ReferencesResult`.
* :func:`collect_references` - query every resolved paper of ``state/discovery.json``,
  cache each answer under ``corpus/<safe_id>/references.json`` and write the run
  report ``state/references.json``.

Rules implemented here:

* All raw references are kept, including those without an arXiv ID. Only references
  with an arXiv ID reach the final ``references`` table (see :mod:`src.storage`).
* Publications are queried once by base arXiv ID. Semantic Scholar does not
  distinguish arXiv versions, so the cached references are *publication-level*
  and apply to every version of the paper.
* Pacing: at least ``s2_delay_seconds`` between requests (3.1 s by default, 1.0 s when
  an API key is configured; never below the documented 1 request/second) and, without
  an API key, at most ``s2_window_max_requests`` requests per ``s2_window_seconds``
  (documented limit: 100 per 5 minutes). 429/5xx/network errors are retried with
  backoff and ``Retry-After``; a 401/403 stops the run (bad API key).
* Outcomes are distinguished: ``success``, ``empty_references`` (API answered with zero
  references), ``not_found`` (HTTP 404) and ``failed`` (request or payload problem). These
  names are the ones ``src/metrics.py`` reads from ``state/references.json``.
* A submission date is never fabricated from a publication year or date: the field
  ``submission_date`` of a reference stays ``None`` until an arXiv date is known.
* The API is paged through the dedicated ``/paper/{id}/references`` endpoint
  (``offset``/``limit``/``next``) so a long reference list is never silently cut.

NOTE: the Semantic Scholar host was not reachable from the environment where this
module was written, so the response shape (``data`` list of ``citedPaper`` objects
and the ``next`` offset) follows the public API documentation and is covered by
offline tests only. Run the smoke test in ``docs/references.md`` once on a real
machine before a long crawl.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests

from .config import CrawlConfig
from .discovery import normalize_id
from .storage import (
    CACHEABLE_STATUSES,
    SCHEMA_VERSION,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_NOT_FOUND,
    STATUS_OK,
    discovery_path,
    load_discovery_papers,
    load_references_cache,
    paper_dirname,
    references_cache_path,
    references_report_path,
    save_references_cache,
    write_json_atomic,
)

API_BASE = "https://api.semanticscholar.org/graph/v1"
SOURCE_NAME = "semantic_scholar_graph_v1"
# Fields of each cited paper (flat names; nested forms such as authors.name are rejected).
REFERENCE_FIELDS = "paperId,title,authors,year,publicationDate,venue,externalIds"
VERSION_NOTE = (
    "Semantic Scholar answers per publication and does not distinguish arXiv versions; "
    "these references apply to every version of the paper."
)
MAX_PAGES = 200
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0
RETRY_AFTER_MAX_SECONDS = 300.0
COVERAGE_FIELDS = (
    "arxiv_id",
    "submission_date",
    "s2_paper_id",
    "title",
    "authors",
    "publication_date",
    "doi",
)


class SemanticScholarAuthError(RuntimeError):
    """HTTP 401/403: the API key is missing, invalid or not allowed. Retrying is pointless."""


class ReferencesRequestError(Exception):
    """A request or payload problem that ends the query of one publication as ``failed``."""

    def __init__(self, error_type: str, message: str, http_status: Optional[int] = None):
        super().__init__(message)
        self.error_type = error_type
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_config(config: CrawlConfig) -> None:
    if config.s2_delay_seconds < 1.0:
        raise ValueError("Semantic Scholar allows 1 request/second: s2_delay_seconds >= 1")
    if config.s2_window_seconds <= 0 or config.s2_window_max_requests < 1:
        raise ValueError("Require s2_window_seconds > 0 and s2_window_max_requests >= 1")
    if not 1 <= config.s2_page_limit <= 1000:
        raise ValueError("s2_page_limit must be between 1 and 1000")
    if config.max_attempts < 1 or config.request_timeout_seconds <= 0:
        raise ValueError("Require max_attempts >= 1 and request_timeout_seconds > 0")


# --------------------------------------------------------------------------- #
# Pacing
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Block until a request may start.

    Enforces a minimum gap between request *starts* and, when ``window_max_requests``
    is set, a sliding window (for example 95 requests per 300 s). Requests that end in
    429 or an error still count, because the server counted them too.
    ``clock`` and ``sleep`` are injectable so tests run without real waiting.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        window_seconds: float = 300.0,
        window_max_requests: int = 0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval_seconds = min_interval_seconds
        self.window_seconds = window_seconds
        self.window_max_requests = window_max_requests
        self._clock = clock
        self._sleep = sleep
        self._starts: Deque[float] = deque()
        self._last_start: Optional[float] = None

    def wait(self) -> None:
        while True:
            now = self._clock()
            if self.window_max_requests:
                while self._starts and self._starts[0] <= now - self.window_seconds + 1e-9:
                    self._starts.popleft()
            delay = 0.0
            if self._last_start is not None:
                delay = self._last_start + self.min_interval_seconds - now
            if self.window_max_requests and len(self._starts) >= self.window_max_requests:
                delay = max(delay, self._starts[0] + self.window_seconds - now)
            if delay <= 1e-9:
                break
            self._sleep(delay)
        self._last_start = now
        if self.window_max_requests:
            self._starts.append(now)


def _parse_retry_after(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def _clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _normalize_reference_arxiv_id(raw: Any) -> Optional[str]:
    """Canonical base arXiv ID, keeping the old ``archive/YYMMNNN`` form; ``None`` if invalid."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return normalize_id(raw)
    except ValueError:
        return None


def normalize_reference(item: Any, index: int) -> Optional[Dict[str, Any]]:
    """Convert one API item (``{"citedPaper": {...}}`` or a bare paper) to a flat record.

    Returns ``None`` for items without a usable paper object. Unknown values are
    ``None`` or empty, never guessed; ``submission_date`` is always ``None`` here.
    """
    paper = item.get("citedPaper") if isinstance(item, dict) and "citedPaper" in item else item
    if not isinstance(paper, dict):
        return None
    external = paper.get("externalIds")
    external = external if isinstance(external, dict) else {}
    arxiv_raw = external.get("ArXiv")
    authors: List[str] = []
    for author in paper.get("authors") or []:
        name = _clean_text(author.get("name")) if isinstance(author, dict) else None
        if name:
            authors.append(name)
    year = paper.get("year")
    if isinstance(year, bool) or not isinstance(year, int):
        year = None
    return {
        "index": index,
        "s2_id": paper.get("paperId") if isinstance(paper.get("paperId"), str) else None,
        "title": _clean_text(paper.get("title")),
        "authors": authors,
        "year": year,
        "publication_date": _clean_text(paper.get("publicationDate")),
        "venue": _clean_text(paper.get("venue")),
        "arxiv_id": _normalize_reference_arxiv_id(arxiv_raw),
        "arxiv_id_raw": arxiv_raw if isinstance(arxiv_raw, str) else None,
        "doi": _clean_text(external.get("DOI")),
        "external_ids": external,
        # Filled only from arXiv metadata. Never derived from year/publicationDate.
        "submission_date": None,
    }


# --------------------------------------------------------------------------- #
# Result record
# --------------------------------------------------------------------------- #
@dataclass
class ReferencesResult:
    arxiv_id: str
    status: str
    references: List[Dict[str, Any]] = field(default_factory=list)
    raw_pages: List[Dict[str, Any]] = field(default_factory=list)
    http_status: Optional[int] = None
    request_count: int = 0
    pages: int = 0
    duration_seconds: float = 0.0
    fetched_at: str = ""
    error_type: Optional[str] = None
    error: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        """The JSON document stored in ``corpus/<safe_id>/references.json``."""
        return {
            "schema_version": SCHEMA_VERSION,
            "arxiv_id": self.arxiv_id,
            "queried_id": f"arXiv:{self.arxiv_id}",
            "source": SOURCE_NAME,
            "granularity": "publication_level",
            "version_note": VERSION_NOTE,
            "status": self.status,
            "http_status": self.http_status,
            "requests": self.request_count,
            "pages": self.pages,
            "fetched_at": self.fetched_at,
            "duration_seconds": round(self.duration_seconds, 4),
            "error_type": self.error_type,
            "error": self.error,
            "counts": {
                "total": len(self.references),
                "with_arxiv_id": sum(1 for r in self.references if r.get("arxiv_id")),
            },
            "references": self.references,
            "raw_pages": self.raw_pages,
        }


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class SemanticScholarClient:
    """Sequential, paced client for the Semantic Scholar Graph API (one connection)."""

    def __init__(
        self,
        config: CrawlConfig,
        *,
        session: Optional[requests.Session] = None,
        limiter: Optional[RateLimiter] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        _validate_config(config)
        self.config = config
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = config.user_agent
        if config.s2_api_key:
            self.session.headers["x-api-key"] = config.s2_api_key
        self._sleep = sleep
        self._limiter = limiter or RateLimiter(
            config.s2_delay_seconds,
            config.s2_window_seconds,
            0 if config.s2_api_key else config.s2_window_max_requests,
            sleep=sleep,
        )
        self.request_count = 0

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))

    def _get(self, url: str, params: Dict[str, Any]) -> tuple:
        """GET with pacing and bounded retries. Returns ``(status, payload)`` for 200/404."""
        attempts = self.config.max_attempts
        for attempt in range(1, attempts + 1):
            self._limiter.wait()
            self.request_count += 1
            try:
                response = self.session.get(
                    url, params=params, timeout=self.config.request_timeout_seconds
                )
            except requests.RequestException as exc:
                if attempt == attempts:
                    raise ReferencesRequestError(type(exc).__name__, str(exc)) from exc
                self._sleep(self._backoff(attempt))
                continue
            status = response.status_code
            if status == 200:
                try:
                    return status, response.json()
                except ValueError as exc:
                    raise ReferencesRequestError(
                        "invalid_json", "Response body is not valid JSON", status
                    ) from exc
            if status == 404:
                return status, None
            if status in (401, 403):
                raise SemanticScholarAuthError(
                    f"Semantic Scholar rejected the request with HTTP {status}; "
                    "check SEMANTIC_SCHOLAR_API_KEY"
                )
            if status == 429 or status >= 500:
                if attempt == attempts:
                    raise ReferencesRequestError(
                        f"http_{status}", f"HTTP {status} after {attempts} attempts", status
                    )
                delay = max(
                    self._backoff(attempt),
                    min(
                        RETRY_AFTER_MAX_SECONDS,
                        _parse_retry_after(response.headers.get("Retry-After")),
                    ),
                )
                self._sleep(delay)
                continue
            raise ReferencesRequestError(f"http_{status}", f"Unexpected HTTP {status}", status)
        raise ReferencesRequestError("attempts_exhausted", "No attempt was made")

    def fetch_references(self, arxiv_id: str) -> ReferencesResult:
        """Fetch every reference of one publication, following pagination to the end."""
        base = normalize_id(arxiv_id)
        started = time.monotonic()
        fetched_at = _now()
        requests_before = self.request_count
        url = f"{API_BASE}/paper/{quote('arXiv:' + base, safe=':')}/references"
        pages: List[Dict[str, Any]] = []
        items: List[Any] = []
        status = STATUS_OK
        http_status: Optional[int] = None
        error_type: Optional[str] = None
        error: Optional[str] = None
        try:
            offset = 0
            while True:
                http_status, payload = self._get(
                    url,
                    {
                        "fields": REFERENCE_FIELDS,
                        "offset": offset,
                        "limit": self.config.s2_page_limit,
                    },
                )
                if http_status == 404:
                    if pages:
                        raise ReferencesRequestError(
                            "page_not_found", f"HTTP 404 at offset {offset}", 404
                        )
                    status = STATUS_NOT_FOUND
                    break
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    raise ReferencesRequestError(
                        "unexpected_response", "Response has no 'data' list", http_status
                    )
                pages.append(payload)
                items.extend(data)
                next_offset = payload.get("next")
                if next_offset is None:
                    break
                if (
                    isinstance(next_offset, bool)
                    or not isinstance(next_offset, int)
                    or next_offset <= offset
                ):
                    raise ReferencesRequestError(
                        "unexpected_response",
                        f"Pagination did not advance (offset={offset}, next={next_offset!r})",
                        http_status,
                    )
                if len(pages) >= MAX_PAGES:
                    raise ReferencesRequestError(
                        "too_many_pages", f"More than {MAX_PAGES} pages", http_status
                    )
                offset = next_offset
        except ReferencesRequestError as exc:
            status = STATUS_FAILED
            error_type, error = exc.error_type, str(exc)
            http_status = exc.http_status or http_status

        references: List[Dict[str, Any]] = []
        if status == STATUS_OK:
            for index, item in enumerate(items):
                normalized = normalize_reference(item, index)
                if normalized is not None:
                    references.append(normalized)
            if not references:
                status = STATUS_EMPTY
        if status in (STATUS_FAILED, STATUS_NOT_FOUND):
            pages = []
        return ReferencesResult(
            arxiv_id=base,
            status=status,
            references=references,
            raw_pages=pages,
            http_status=http_status,
            request_count=self.request_count - requests_before,
            pages=len(pages),
            duration_seconds=time.monotonic() - started,
            fetched_at=fetched_at,
            error_type=error_type,
            error=error,
        )

    def close(self) -> None:
        self.session.close()


def fetch_references(
    arxiv_id: str,
    config: CrawlConfig,
    *,
    client: Optional[SemanticScholarClient] = None,
) -> ReferencesResult:
    """Query one publication. Pass a shared ``client`` to keep pacing across calls."""
    owned = client is None
    client = client or SemanticScholarClient(config)
    try:
        return client.fetch_references(arxiv_id)
    finally:
        if owned:
            client.close()


# --------------------------------------------------------------------------- #
# Batch collection with resume
# --------------------------------------------------------------------------- #
def _count_fields(references: Iterable[Dict[str, Any]], counts: Counter) -> None:
    for ref in references:
        counts["arxiv_id"] += bool(ref.get("arxiv_id"))
        counts["submission_date"] += bool(ref.get("submission_date"))
        counts["s2_paper_id"] += bool(ref.get("s2_id"))
        counts["title"] += bool(ref.get("title"))
        counts["authors"] += bool(ref.get("authors"))
        counts["publication_date"] += bool(ref.get("publication_date"))
        counts["doi"] += bool(ref.get("doi"))


def _entry(
    config: CrawlConfig,
    base: str,
    record: Dict[str, Any],
    *,
    cache_hit: bool,
    seconds: float,
) -> Dict[str, Any]:
    counts = record.get("counts") or {}
    cached_status = record.get("status") in CACHEABLE_STATUSES
    return {
        "arxiv_id": base,
        "safe_id": paper_dirname(base),
        "status": record["status"],
        # Names below match what src/metrics.py reads from state/references.json.
        "total_references_count": counts.get("total", 0),
        "arxiv_references_count": counts.get("with_arxiv_id", 0),
        "pages": record.get("pages", 0),
        "requests": 0 if cache_hit else record.get("requests", 0),
        "http_status": record.get("http_status"),
        "cache_hit": cache_hit,
        "fetched_at": record.get("fetched_at"),
        # Time the API calls for this paper took when it was fetched (kept on resume,
        # so per-paper cost is not understated by cache hits); run_seconds is this run only.
        "duration_seconds": round(float(record.get("duration_seconds") or 0.0), 4),
        "run_seconds": round(seconds, 4),
        "references_path": (
            references_cache_path(config, base).relative_to(config.output_dir).as_posix()
            if cached_status
            else None
        ),
        "error_type": record.get("error_type"),
        "error": record.get("error"),
    }


def _summarize(
    entries: List[Dict[str, Any]],
    assigned: int,
    field_counts: Counter,
    aborted: Optional[str],
) -> Dict[str, Any]:
    by_status = Counter(e["status"] for e in entries)
    queried = len(entries)
    successful = by_status[STATUS_OK] + by_status[STATUS_EMPTY]
    total_refs = sum(e["total_references_count"] for e in entries)
    total_arxiv = sum(e["arxiv_references_count"] for e in entries)

    def ratio(numerator: float, denominator: float) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    return {
        "assigned_papers": assigned,
        "queried_papers": queried,
        "not_crawled_papers": assigned - queried,
        "successful_api_queries": successful,
        "papers_with_references": by_status[STATUS_OK],
        "empty_references_papers": by_status[STATUS_EMPTY],
        "not_found_papers": by_status[STATUS_NOT_FOUND],
        "failed_queries": by_status[STATUS_FAILED],
        "cache_hits": sum(1 for e in entries if e["cache_hit"]),
        "requests_this_run": sum(e["requests"] for e in entries),
        "reference_scrape_success_rate": ratio(successful, queried),
        "total_references_crawled": total_refs,
        "total_arxiv_references_crawled": total_arxiv,
        "avg_references_per_queried_paper": ratio(total_refs, queried),
        "avg_arxiv_references_per_queried_paper": ratio(total_arxiv, queried),
        "avg_references_per_successful_paper": ratio(total_refs, successful),
        "avg_arxiv_references_per_successful_paper": ratio(total_arxiv, successful),
        "field_coverage_rates": {
            name: ratio(field_counts[name], total_refs) for name in COVERAGE_FIELDS
        },
        "aborted": aborted,
    }


def collect_references(
    config: CrawlConfig,
    *,
    ids: Optional[Iterable[str]] = None,
    discovery_file: Optional[Path] = None,
    resume: bool = False,
    retry_not_found: bool = False,
    client: Optional[SemanticScholarClient] = None,
    checkpoint_every: int = 100,
    progress: Optional[Callable[[str], None]] = print,
) -> Dict[str, Any]:
    """Collect references for every resolved paper (or for explicit ``ids``).

    With ``resume=True`` a paper whose cache holds ``success``, ``empty_references`` or ``not_found``
    is not queried again (``retry_not_found=True`` re-queries ``not_found``); papers
    that ``failed`` or were never queried are queried. Each answer is cached
    atomically before the report mentions it, so an interrupted run loses at most the
    paper in flight. The report ``state/references.json`` is checkpointed every
    ``checkpoint_every`` papers and again on exit, including on Ctrl+C.
    """
    _validate_config(config)
    if ids is None:
        source = Path(discovery_file) if discovery_file else discovery_path(config)
        bases = [p["arxiv_id"] for p in load_discovery_papers(source)]
        source_label = str(source)
    else:
        bases = list(ids)
        source_label = "explicit ids"
    bases = list(dict.fromkeys(normalize_id(value) for value in bases))

    report_path = references_report_path(config)
    started = time.monotonic()
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "started_at": _now(),
        "source": source_label,
        "api": SOURCE_NAME,
        "granularity": "publication_level",
        "authenticated": bool(config.s2_api_key),
        "complete": False,
        "papers": [],
    }
    entries: List[Dict[str, Any]] = report["papers"]
    field_counts: Counter = Counter()
    aborted: Optional[str] = None
    finished = False

    owned = client is None
    client = client or SemanticScholarClient(config)

    def snapshot() -> None:
        report["summary"] = _summarize(entries, len(bases), field_counts, aborted)
        report["elapsed_seconds"] = round(time.monotonic() - started, 4)
        report["complete"] = finished
        write_json_atomic(report_path, report)

    try:
        total = len(bases)
        for position, base in enumerate(bases, 1):
            paper_start = time.monotonic()
            cached = load_references_cache(config, base) if resume else None
            reuse = cached is not None and not (
                retry_not_found and cached["status"] == STATUS_NOT_FOUND
            )
            if reuse:
                record = cached
            else:
                try:
                    result = client.fetch_references(base)
                except SemanticScholarAuthError as exc:
                    aborted = str(exc)
                    if progress:
                        progress(f"[References {position}/{total}] {base}: aborted ({exc})")
                    break
                record = result.to_record()
                if result.status in CACHEABLE_STATUSES:
                    save_references_cache(config, record)
            if record["status"] == STATUS_OK:
                _count_fields(record["references"], field_counts)
            entry = _entry(
                config, base, record, cache_hit=reuse, seconds=time.monotonic() - paper_start
            )
            entries.append(entry)
            if progress:
                note = " (cached)" if reuse else ""
                detail = (
                    f"{entry['total_references_count']} refs, {entry['arxiv_references_count']} with arXiv ID"
                    if record["status"] != STATUS_FAILED
                    else f"{entry['error_type']}"
                )
                progress(f"[References {position}/{total}] {base}: {entry['status']} ({detail}){note}")
            if checkpoint_every and len(entries) % checkpoint_every == 0:
                snapshot()
        else:
            finished = True
    finally:
        try:
            snapshot()
        finally:
            if owned:
                client.close()
    return report


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def config_from_environment(
    output: Path, *, timeout: float = 60.0, attempts: int = 3
) -> CrawlConfig:
    """Build a config from ``.env`` / environment variables (never logs the key)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip() or None
    return CrawlConfig(
        output_dir=output,
        request_timeout_seconds=timeout,
        max_attempts=attempts,
        user_agent=os.getenv("ARXIV_USER_AGENT", "Lab1ArxivCrawler/0.1"),
        s2_api_key=key,
        # 1 request/second with an API key; the safer default (3.1 s) without one.
        **({"s2_delay_seconds": 1.0} if key else {}),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Semantic Scholar references for the papers in "
            "<output>/state/discovery.json, or for the IDs given with --arxiv-id."
        )
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--discovery-path", type=Path, default=None)
    parser.add_argument(
        "--arxiv-id",
        nargs="+",
        default=None,
        metavar="ID",
        help="Query these base IDs instead of discovery.json (smoke test)",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse cached success/empty_references/not_found")
    parser.add_argument(
        "--retry-not-found", action="store_true", help="With --resume, query not_found again"
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    config = config_from_environment(args.output, timeout=args.timeout, attempts=args.attempts)
    report = collect_references(
        config,
        ids=args.arxiv_id,
        discovery_file=args.discovery_path,
        resume=args.resume,
        retry_not_found=args.retry_not_found,
    )
    print("References summary:", json.dumps(report["summary"], ensure_ascii=False))
    print(f"References output: {references_report_path(config)}")
    if report["summary"]["failed_queries"] or report["summary"]["aborted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
