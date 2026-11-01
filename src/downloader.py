"""Member 2: download each version from https://export.arxiv.org/src/{id}v{n}.

Workflow enforced by this module:
1. Assigned paper IDs (or ``start_month - start_id - end_month - end_id`` ranges
   converted to ``data/assigned_ids.txt``) MUST first be passed to Member 1's
   discovery module (:func:`src.discovery.discover_papers` / ``python -m src.main --discover``)
   to identify valid papers, resolve metadata via the arXiv API, and enumerate
   all versions ``v1..vN`` in ``data/state/discovery.json``.
2. Only after discovery has identified the versions does the downloader fetch
   each version's source package from ``https://export.arxiv.org/src/{id}v{n}``
   via :func:`download_source` and :func:`download_discovered_sources`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from .config import CrawlConfig
from .discovery import (
    DEFAULT_MEMBER_QUOTA,
    ArxivClient,
    discover_papers,
    expand_assigned_range,
    format_arxiv_id,
    load_assigned_ranges,
    parse_assigned_range_line,
    parse_year_month,
    prepare_assigned_ids,
    write_json,
)

ARXIV_SRC_BASE_URL = "https://export.arxiv.org/src"
CHUNK_SIZE = 64 * 1024
SAMPLE_SIZE = 64 * 1024

VERSIONED_ID_PATTERN = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7}))v(?P<version>[1-9]\d*)$",
    re.IGNORECASE,
)

# Module-level timestamp (time.monotonic()) so consecutive calls to download_source()
# still respect the >= 3s arXiv etiquette even when no shared client is passed.
_LAST_ARXIV_REQUEST_TS: Optional[float] = None


def _reset_pacing_for_tests() -> None:
    """Reset the module-level pacing timestamp (intended for unit tests only)."""
    global _LAST_ARXIV_REQUEST_TS
    _LAST_ARXIV_REQUEST_TS = None


def parse_version_id(version_id: str) -> Tuple[str, int, str]:
    """Validate and split a versioned arXiv identifier.

    Returns ``(base_id, version_number, canonical_version_id)``.
    Old-style archive prefixes (e.g. ``hep-th/9901001v1``) are normalized to
    lowercase archive names while preserving the 7-digit number.
    """
    if not isinstance(version_id, str):
        raise ValueError(f"Invalid versioned arXiv ID: {version_id!r}")
    cleaned = version_id.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[6:].strip()
    match = VERSIONED_ID_PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(
            f"Expected versioned arXiv ID with explicit 'v<N>' suffix, got: {version_id!r}"
        )
    base = match.group("base")
    if "/" in base:
        archive, number = base.split("/", 1)
        base = f"{archive.lower()}/{number}"
    version = int(match.group("version"))
    canonical = f"{base}v{version}"
    return base, version, canonical


def safe_paper_dirname(arxiv_id: str) -> str:
    """Convert an arXiv base ID into a safe single-component folder name.

    Examples:
        ``2310.12345`` -> ``2310-12345``
        ``hep-th/9901001`` -> ``hep-th-9901001``
    """
    cleaned = arxiv_id.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[6:].strip()
    cleaned = re.sub(r"v[1-9]\d*$", "", cleaned, flags=re.IGNORECASE)
    if "/" in cleaned:
        archive, rest = cleaned.split("/", 1)
        cleaned = f"{archive.lower()}/{rest}"
    if not cleaned or ".." in cleaned or "\\" in cleaned:
        raise ValueError(f"Unsafe arXiv ID for directory mapping: {arxiv_id!r}")
    safe = cleaned.replace("/", "-").replace(".", "-")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", safe):
        raise ValueError(f"Unsafe mapped directory name {safe!r} from {arxiv_id!r}")
    return safe


def build_source_url(version_id: str) -> str:
    """Return the canonical export.arxiv.org source URL for a versioned ID."""
    _, _, canonical = parse_version_id(version_id)
    return f"{ARXIV_SRC_BASE_URL}/{canonical}"


def _validate_source_url(source_url: str) -> str:
    """Ensure source URLs point to the export.arxiv.org mirror over HTTP(S)."""
    parsed = urlparse(source_url)
    if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != "export.arxiv.org":
        raise ValueError(
            f"Source downloads must use the export.arxiv.org mirror, got: {source_url!r}"
        )
    if not parsed.path.startswith("/src/"):
        raise ValueError(f"Expected /src/ endpoint in source URL, got: {source_url!r}")
    return source_url


def _validate_config(config: CrawlConfig) -> None:
    if config.arxiv_delay_seconds < 3.0:
        raise ValueError("Require arxiv_delay_seconds >= 3.0")
    if config.request_timeout_seconds <= 0:
        raise ValueError("Require request_timeout_seconds > 0")
    if config.max_attempts < 1:
        raise ValueError("Require max_attempts >= 1")
    if not config.user_agent or not config.user_agent.strip():
        raise ValueError("Require a non-empty descriptive user_agent")


@dataclass
class DownloadResult:
    """Structured outcome of a single version source download attempt.

    Supports both attribute access (``result.status``) and mapping access
    (``result["status"]``) for smooth integration across pipeline modules.
    """

    version_id: str
    arxiv_id: str
    version: int
    source_url: str
    status: str
    source_path: Optional[Path] = None
    state_path: Optional[Path] = None
    bytes_downloaded: int = 0
    sha256: Optional[str] = None
    content_type: Optional[str] = None
    detected_format: Optional[str] = None
    http_status: Optional[int] = None
    attempts: int = 0
    duration_seconds: float = 0.0
    cache_hit: bool = False
    downloaded_at: Optional[str] = None
    error_type: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "arxiv_id": self.arxiv_id,
            "version": self.version,
            "source_url": self.source_url,
            "status": self.status,
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "state_path": str(self.state_path) if self.state_path is not None else None,
            "bytes_downloaded": self.bytes_downloaded,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "detected_format": self.detected_format,
            "http_status": self.http_status,
            "attempts": self.attempts,
            "duration_seconds": self.duration_seconds,
            "cache_hit": self.cache_hit,
            "downloaded_at": self.downloaded_at,
            "error_type": self.error_type,
            "error": self.error,
        }

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key in ("source_path", "state_path"):
            return getattr(self, key)
        return data[key]

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return self.to_dict().keys()

    def items(self):
        data = self.to_dict()
        data["source_path"] = self.source_path
        data["state_path"] = self.state_path
        return data.items()

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()


def _parse_retry_after(header_value: Optional[str]) -> float:
    """Parse a Retry-After header (seconds or HTTP-date) into a non-negative delay."""
    if not header_value:
        return 0.0
    value = header_value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (target - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _has_binary_archive_signature(sample: bytes) -> bool:
    return (
        sample.startswith(b"\x1f\x8b")  # gzip
        or sample.startswith(b"BZh")  # bzip2
        or sample.startswith(b"\xfd7zXZ\x00")  # xz
        or (len(sample) >= 262 and sample[257:262] == b"ustar")  # POSIX tar
    )


def _looks_like_html(sample: bytes, content_type: Optional[str]) -> bool:
    """Detect HTML error/captcha/throttling pages regardless of file extension."""
    if _has_binary_archive_signature(sample) or sample.startswith(b"%PDF-"):
        return False

    ctype = (content_type or "").lower()
    if "text/html" in ctype or "application/xhtml+xml" in ctype:
        return True

    stripped = sample.lstrip()
    if not stripped or b"\x00" in stripped[:1024]:
        return False

    head_text = stripped[:2048].decode("utf-8", errors="ignore")
    lowered = head_text.lower()

    tex_markers = (
        r"\documentclass",
        r"\begin{document}",
        r"\usepackage",
        r"\input{",
        r"\include{",
        r"\section{",
    )
    if any(marker in head_text for marker in tex_markers):
        return False

    html_prefixes = (
        "<!doctype html",
        "<html",
        "<head",
        "<title",
        "<body",
        "<!--",
    )
    if lowered.startswith(html_prefixes):
        return True
    if lowered.startswith("<?xml") and ("<html" in lowered or "<error" in lowered):
        return True
    if "<html" in lowered and "</html>" in lowered:
        return True
    return False


def _looks_like_throttling_html(sample: bytes) -> bool:
    """Check whether an HTML payload indicates rate limiting or temporary unavailability."""
    text = sample[:4096].decode("utf-8", errors="ignore").lower()
    throttling_keywords = (
        "rate limit",
        "too many requests",
        "exceeded",
        "temporarily unavailable",
        "service unavailable",
        "try again later",
        "captcha",
        "automated",
        "robot",
    )
    return any(keyword in text for keyword in throttling_keywords)


def _detect_payload_format(
    file_path: Path, sample: bytes, content_type: Optional[str]
) -> Tuple[str, str]:
    """Classify a downloaded file and return ``(detected_format, file_extension)``.

    Assigning ``.tex`` or ``.bib`` extensions to plain-text submissions ensures
    direct compatibility with :func:`src.extractor.extract_source`.
    """
    try:
        with tarfile.open(file_path, mode="r:*") as archive:
            archive.getmembers()
        if sample.startswith(b"\x1f\x8b"):
            return "tar_archive", ".tar.gz"
        if sample.startswith(b"BZh"):
            return "tar_archive", ".tar.bz2"
        if sample.startswith(b"\xfd7zXZ\x00"):
            return "tar_archive", ".tar.xz"
        return "tar_archive", ".tar"
    except (tarfile.TarError, EOFError, OSError):
        pass

    if sample.startswith(b"%PDF-"):
        return "pdf", ".pdf"

    if sample.startswith(b"\x1f\x8b"):
        return "gzip_file", ".gz"

    if _has_binary_archive_signature(sample):
        return "corrupt_archive", ".bin"

    if b"\x00" not in sample:
        text = sample.decode("utf-8", errors="ignore")
        lowered = text.lower()
        ctype = (content_type or "").lower()
        tex_markers = (
            r"\documentclass",
            r"\begin{document}",
            r"\usepackage",
            r"\include{",
            r"\input{",
            r"\section",
            r"\def",
            r"\newcommand",
        )
        bib_markers = ("@article", "@book", "@inproceedings", "@misc", "@techreport")
        if "tex" in ctype or any(marker in text for marker in tex_markers):
            return "plain_tex", ".tex"
        if "bib" in ctype or any(marker in lowered for marker in bib_markers):
            return "plain_bib", ".bib"
        if text.lstrip().startswith("%"):
            return "plain_tex", ".tex"

    return "unknown", ".bin"


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_cached_download(
    state_path: Path, expected_version_id: str
) -> Optional[DownloadResult]:
    """Verify both the state JSON and the on-disk file before skipping a download."""
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(state, dict):
        return None
    if state.get("status") != "downloaded":
        return None
    if state.get("version_id") != expected_version_id:
        return None

    raw_source_path = state.get("source_path")
    if not raw_source_path or not isinstance(raw_source_path, str):
        return None

    source_path = Path(raw_source_path)
    if source_path.suffix.lower() in (".part", ".tmp"):
        return None
    if not source_path.is_file():
        return None

    try:
        actual_size = source_path.stat().st_size
    except OSError:
        return None

    expected_size = state.get("bytes_downloaded")
    if not isinstance(expected_size, int) or expected_size <= 0 or actual_size != expected_size:
        return None

    expected_sha256 = state.get("sha256")
    if not expected_sha256 or not isinstance(expected_sha256, str):
        return None
    try:
        actual_sha256 = _compute_sha256(source_path)
    except OSError:
        return None
    if actual_sha256 != expected_sha256:
        return None

    base_id, version_num, _ = parse_version_id(expected_version_id)
    return DownloadResult(
        version_id=expected_version_id,
        arxiv_id=state.get("arxiv_id") or base_id,
        version=int(state.get("version") or version_num),
        source_url=state.get("source_url") or build_source_url(expected_version_id),
        status="downloaded",
        source_path=source_path,
        state_path=state_path,
        bytes_downloaded=actual_size,
        sha256=actual_sha256,
        content_type=state.get("content_type"),
        detected_format=state.get("detected_format"),
        http_status=state.get("http_status", 200),
        attempts=0,
        duration_seconds=0.0,
        cache_hit=True,
        downloaded_at=state.get("downloaded_at"),
        error_type=None,
        error=None,
    )


def _wait_for_arxiv_slot(delay_seconds: float, client: Any = None) -> None:
    """Enforce >= delay_seconds between consecutive arXiv requests across client and module."""
    global _LAST_ARXIV_REQUEST_TS

    candidates = []
    if _LAST_ARXIV_REQUEST_TS is not None:
        candidates.append(_LAST_ARXIV_REQUEST_TS)
    if client is not None and getattr(client, "last_request", None) is not None:
        candidates.append(float(client.last_request))

    if candidates:
        last_ts = max(candidates)
        remaining = delay_seconds - (time.monotonic() - last_ts)
        if remaining > 0:
            time.sleep(remaining)

    now = time.monotonic()
    _LAST_ARXIV_REQUEST_TS = now
    if client is not None and hasattr(client, "last_request"):
        client.last_request = now


def _mark_request_completed(client: Any = None) -> None:
    """Update pacing timestamp when a streaming response finishes."""
    global _LAST_ARXIV_REQUEST_TS
    now = time.monotonic()
    _LAST_ARXIV_REQUEST_TS = now
    if client is not None and hasattr(client, "last_request"):
        client.last_request = now


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def download_source(
    version_id: str,
    config: CrawlConfig,
    *,
    resume: bool = False,
    client: Any = None,
    source_url: Optional[str] = None,
) -> DownloadResult:
    """Download the source package for a single arXiv version.

    Parameters
    ----------
    version_id:
        Versioned arXiv ID such as ``"2310.12345v1"`` or ``"hep-th/9901001v2"``.
    config:
        Shared :class:`~src.config.CrawlConfig` specifying output directory,
        pacing (>= 3.0s), timeout, max attempts, and ``User-Agent``.
    resume:
        When ``True``, reuse an existing download only if both the state record
        and the target file exist, are non-empty, and match the recorded size
        and SHA-256 checksum. Temporary ``.part`` files are never treated as complete.
    client:
        Optional shared :class:`~src.discovery.ArxivClient` (or object exposing
        ``session`` and ``last_request``) to coordinate pacing across modules.
    source_url:
        Optional explicit URL (must target ``https://export.arxiv.org/src/...``).
    """
    _validate_config(config)
    base_id, version_num, canonical_version_id = parse_version_id(version_id)
    target_url = (
        _validate_source_url(source_url)
        if source_url is not None
        else build_source_url(canonical_version_id)
    )

    safe_paper = safe_paper_dirname(base_id)
    download_dir = config.output_dir / "tmp" / "downloads" / safe_paper
    state_path = config.output_dir / "state" / "downloads" / safe_paper / f"v{version_num}.json"
    part_path = download_dir / f"v{version_num}.part"

    started = time.monotonic()

    if resume:
        cached = _verify_cached_download(state_path, canonical_version_id)
        if cached is not None:
            cached.duration_seconds = time.monotonic() - started
            return cached

    download_dir.mkdir(parents=True, exist_ok=True)
    _remove_quietly(part_path)

    owned_session = False
    if client is not None and hasattr(client, "session"):
        session = client.session
        session.headers["User-Agent"] = config.user_agent
    elif isinstance(client, requests.Session):
        session = client
        session.headers["User-Agent"] = config.user_agent
    else:
        session = requests.Session()
        session.headers["User-Agent"] = config.user_agent
        owned_session = True

    attempts = 0
    last_http_status: Optional[int] = None
    last_content_type: Optional[str] = None
    final_status = "failed"
    final_error_type: Optional[str] = None
    final_error: Optional[str] = None

    try:
        for attempt in range(config.max_attempts):
            attempts = attempt + 1
            _remove_quietly(part_path)
            _wait_for_arxiv_slot(config.arxiv_delay_seconds, client=client)

            try:
                response = session.get(
                    target_url,
                    stream=True,
                    timeout=config.request_timeout_seconds,
                )
            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as exc:
                _mark_request_completed(client=client)
                final_status = "failed"
                final_error_type = type(exc).__name__
                final_error = str(exc)
                if attempt + 1 < config.max_attempts:
                    time.sleep(3.0 * (2**attempt))
                    continue
                break

            try:
                last_http_status = response.status_code
                last_content_type = response.headers.get("Content-Type")

                if response.status_code == 404:
                    _mark_request_completed(client=client)
                    final_status = "not_found"
                    final_error_type = "NotFoundError"
                    final_error = f"Source not found (HTTP 404) for {canonical_version_id}"
                    break

                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    _mark_request_completed(client=client)
                    final_status = "failed"
                    final_error_type = "HTTPError"
                    final_error = f"HTTP {response.status_code} from {target_url}"
                    if attempt + 1 < config.max_attempts:
                        backoff = max(3.0 * (2**attempt), retry_after)
                        time.sleep(backoff)
                        continue
                    break

                if response.status_code == 403:
                    body_preview = b""
                    try:
                        for chunk in response.iter_content(chunk_size=4096):
                            if chunk:
                                body_preview += chunk
                                if len(body_preview) >= 4096:
                                    break
                    except requests.RequestException:
                        pass
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    _mark_request_completed(client=client)
                    final_status = "failed"
                    final_error_type = "HTTPError"
                    final_error = f"HTTP 403 Forbidden for {canonical_version_id}"
                    if (
                        attempt + 1 < config.max_attempts
                        and (_looks_like_throttling_html(body_preview) or retry_after > 0)
                    ):
                        backoff = max(3.0 * (2**attempt), retry_after)
                        time.sleep(backoff)
                        continue
                    break

                if response.status_code != 200:
                    _mark_request_completed(client=client)
                    final_status = "failed"
                    final_error_type = "HTTPError"
                    final_error = f"Unexpected HTTP {response.status_code} for {canonical_version_id}"
                    break

                # Stream body to temporary .part file while computing SHA-256 and capturing sample
                digest = hashlib.sha256()
                sample_chunks = bytearray()
                bytes_written = 0

                try:
                    with part_path.open("wb") as part_file:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue
                            part_file.write(chunk)
                            digest.update(chunk)
                            bytes_written += len(chunk)
                            if len(sample_chunks) < SAMPLE_SIZE:
                                needed = SAMPLE_SIZE - len(sample_chunks)
                                sample_chunks.extend(chunk[:needed])
                except (requests.RequestException, OSError) as exc:
                    _mark_request_completed(client=client)
                    _remove_quietly(part_path)
                    final_status = "failed"
                    final_error_type = type(exc).__name__
                    final_error = str(exc)
                    if attempt + 1 < config.max_attempts:
                        time.sleep(3.0 * (2**attempt))
                        continue
                    break

                _mark_request_completed(client=client)
                sample = bytes(sample_chunks)

                if bytes_written == 0:
                    _remove_quietly(part_path)
                    final_status = "invalid_response"
                    final_error_type = "EmptyResponseError"
                    final_error = f"Empty 0-byte response from {target_url}"
                    if attempt + 1 < config.max_attempts:
                        time.sleep(3.0 * (2**attempt))
                        continue
                    break

                if _looks_like_html(sample, last_content_type):
                    _remove_quietly(part_path)
                    final_status = "invalid_response"
                    final_error_type = "HTMLResponseError"
                    final_error = (
                        f"Received HTML page instead of source package for {canonical_version_id}"
                    )
                    if attempt + 1 < config.max_attempts and _looks_like_throttling_html(sample):
                        time.sleep(3.0 * (2**attempt))
                        continue
                    break

                detected_format, extension = _detect_payload_format(
                    part_path, sample, last_content_type
                )
                final_path = download_dir / f"v{version_num}{extension}"

                # Remove any stale files with different extensions for this version before replacing
                for existing in download_dir.glob(f"v{version_num}.*"):
                    if existing != part_path and existing != final_path:
                        _remove_quietly(existing)

                part_path.replace(final_path)
                downloaded_at = datetime.now(timezone.utc).isoformat()
                duration = time.monotonic() - started

                result = DownloadResult(
                    version_id=canonical_version_id,
                    arxiv_id=base_id,
                    version=version_num,
                    source_url=target_url,
                    status="downloaded",
                    source_path=final_path,
                    state_path=state_path,
                    bytes_downloaded=bytes_written,
                    sha256=digest.hexdigest(),
                    content_type=last_content_type,
                    detected_format=detected_format,
                    http_status=last_http_status,
                    attempts=attempts,
                    duration_seconds=duration,
                    cache_hit=False,
                    downloaded_at=downloaded_at,
                    error_type=None,
                    error=None,
                )
                write_json(state_path, result.to_dict())
                return result
            finally:
                response.close()
    finally:
        _remove_quietly(part_path)
        if owned_session:
            session.close()

    duration = time.monotonic() - started
    failure_result = DownloadResult(
        version_id=canonical_version_id,
        arxiv_id=base_id,
        version=version_num,
        source_url=target_url,
        status=final_status,
        source_path=None,
        state_path=state_path,
        bytes_downloaded=0,
        sha256=None,
        content_type=last_content_type,
        detected_format=None,
        http_status=last_http_status,
        attempts=attempts,
        duration_seconds=duration,
        cache_hit=False,
        downloaded_at=None,
        error_type=final_error_type,
        error=final_error,
    )
    write_json(state_path, failure_result.to_dict())
    return failure_result


def download_discovered_sources(
    config: CrawlConfig,
    *,
    discovery_path: Optional[Path] = None,
    resume: bool = False,
    client: Any = None,
) -> Dict[str, Any]:
    """Download all versions identified in ``state/discovery.json`` without mutating discovery state.

    Requires that discovery (:func:`src.discovery.discover_papers`) has already
    run and produced ``state/discovery.json``. Writes an atomic progress and
    summary snapshot to ``state/downloads.json``.
    """
    _validate_config(config)
    source_discovery_path = discovery_path or (config.output_dir / "state" / "discovery.json")
    if not source_discovery_path.is_file():
        raise FileNotFoundError(
            f"Discovery state file not found: {source_discovery_path}. "
            "Run discovery first to identify papers and versions before downloading."
        )

    discovery_report = json.loads(source_discovery_path.read_text(encoding="utf-8"))
    report_path = config.output_dir / "state" / "downloads.json"
    started = time.monotonic()

    report: Dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "discovery_path": str(source_discovery_path),
        "versions": [],
    }

    owned_client = False
    if client is None:
        client = ArxivClient(config)
        owned_client = True

    try:
        for paper in discovery_report.get("papers", []):
            if paper.get("status") != "resolved":
                continue
            for version_entry in paper.get("versions", []):
                version_id = version_entry["version_id"]
                source_url = version_entry.get("source_url")
                outcome = download_source(
                    version_id,
                    config,
                    resume=resume,
                    client=client,
                    source_url=source_url,
                )
                report["versions"].append(outcome.to_dict())
                report["elapsed_seconds"] = time.monotonic() - started
                write_json(report_path, report)
    finally:
        if owned_client and hasattr(client, "close"):
            client.close()

    versions = report["versions"]
    report["summary"] = {
        "expected_versions": len(versions),
        "downloaded": sum(1 for v in versions if v["status"] == "downloaded"),
        "not_found": sum(1 for v in versions if v["status"] == "not_found"),
        "invalid_response": sum(1 for v in versions if v["status"] == "invalid_response"),
        "failed": sum(1 for v in versions if v["status"] == "failed"),
        "cache_hits": sum(1 for v in versions if v["cache_hit"]),
        "total_bytes_downloaded": sum(v["bytes_downloaded"] for v in versions),
    }
    report["elapsed_seconds"] = time.monotonic() - started
    write_json(report_path, report)
    return report


def discover_and_download(
    ids: Iterable[str],
    config: CrawlConfig,
    *,
    resume: bool = False,
    client: Any = None,
) -> Dict[str, Any]:
    """Run Phase 1 (Discovery/Identify) on ``ids`` first, then Phase 2 (Download).

    1. Calls :func:`src.discovery.discover_papers` so all ``ids`` are resolved
       against the arXiv API and saved to ``state/discovery.json``.
    2. Calls :func:`download_discovered_sources` using the shared ``ArxivClient``
       (preserving the >= 3-second pacing between the last discovery request and
       the first source download) to fetch every version identified in ``state/discovery.json``.
    """
    global _LAST_ARXIV_REQUEST_TS
    _validate_config(config)

    owned_client = False
    if client is None:
        client = ArxivClient(config)
        owned_client = True

    try:
        if _LAST_ARXIV_REQUEST_TS is not None and (
            getattr(client, "last_request", None) is None
            or _LAST_ARXIV_REQUEST_TS > client.last_request
        ):
            client.last_request = _LAST_ARXIV_REQUEST_TS

        discovery_report = discover_papers(ids, config, resume=resume, client=client)
        if getattr(client, "last_request", None) is not None:
            _LAST_ARXIV_REQUEST_TS = float(client.last_request)

        discovery_path = config.output_dir / "state" / "discovery.json"
        download_report = download_discovered_sources(
            config,
            discovery_path=discovery_path,
            resume=resume,
            client=client,
        )
        return {
            "discovery": discovery_report,
            "downloads": download_report,
        }
    finally:
        if owned_client and hasattr(client, "close"):
            client.close()


def main() -> None:
    """CLI for preparing assigned IDs from range, or downloading after Discovery."""
    parser = argparse.ArgumentParser(
        description=(
            "Prepare assigned IDs for Discovery, or download versioned sources "
            "from data/state/discovery.json after Discovery has identified them."
        )
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--prepare-ids",
        action="store_true",
        help="Only expand --range / --range-file into --ids-out (for running main.py --discover next)",
    )
    parser.add_argument(
        "--ids-out",
        type=Path,
        default=None,
        help="Output path for expanded assigned_ids.txt (defaults to <output>/assigned_ids.txt)",
    )
    parser.add_argument(
        "--range",
        nargs=4,
        metavar=("START_MONTH", "START_ID", "END_MONTH", "END_ID"),
        help="Assigned range, e.g. 2025-10 9401 2025-10 14400",
    )
    parser.add_argument(
        "--range-file",
        type=Path,
        help="TSV/text file with start_month start_id end_month end_id rows",
    )
    parser.add_argument(
        "--member-index",
        type=int,
        help="1-based row index to select from --range-file (e.g. 2 for Member 2)",
    )
    parser.add_argument(
        "--expected-total",
        type=int,
        default=DEFAULT_MEMBER_QUOTA,
        help="Total papers per assigned range for cross-month calculation (default: 5000)",
    )
    parser.add_argument(
        "--discovery-path",
        type=Path,
        default=None,
        help="Path to discovery.json (defaults to <output>/state/discovery.json)",
    )
    parser.add_argument("--resume", action="store_true", help="Reuse verified state")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    config = CrawlConfig(
        output_dir=args.output,
        request_timeout_seconds=args.timeout,
        max_attempts=args.attempts,
        user_agent=os.getenv("ARXIV_USER_AGENT", "Lab1ArxivCrawler/0.1"),
    )
    ids_out_path = args.ids_out or (config.output_dir / "assigned_ids.txt")

    selected_range: Optional[Dict[str, Any]] = None
    if args.range:
        selected_range = parse_assigned_range_line(" ".join(args.range))
    elif args.range_file:
        ranges = load_assigned_ranges(args.range_file)
        if not ranges:
            parser.error("No valid range rows found in --range-file")
        if args.member_index is None:
            parser.error("--range-file requires --member-index (1..5)")
        if not (1 <= args.member_index <= len(ranges)):
            parser.error(
                f"--member-index {args.member_index} out of bounds (1..{len(ranges)})"
            )
        selected_range = ranges[args.member_index - 1]

    if selected_range is not None:
        ids = prepare_assigned_ids(
            selected_range["start_month"],
            selected_range["start_id"],
            selected_range["end_month"],
            selected_range["end_id"],
            ids_out_path,
            expected_total=args.expected_total,
        )
        print(f"Prepared {len(ids)} assigned IDs ({ids[0]} .. {ids[-1]}): {ids_out_path}")
        if args.prepare_ids:
            print(
                f"Next step: run discovery first via:\n"
                f"  python -m src.main --discover --ids {ids_out_path} --output {config.output_dir}"
            )
            return

        # Step 1: Identify via Discovery first -> Step 2: Download from discovery.json
        combined = discover_and_download(ids, config, resume=args.resume)
        print("Discovery summary:", json.dumps(combined["discovery"]["summary"]))
        print("Download summary:", json.dumps(combined["downloads"]["summary"]))
        return

    if args.prepare_ids:
        parser.error("--prepare-ids requires --range or --range-file --member-index N")

    # Default mode: download from existing data/state/discovery.json produced by Member 1's discovery
    report = download_discovered_sources(
        config,
        discovery_path=args.discovery_path,
        resume=args.resume,
    )
    print("Download summary:", json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
