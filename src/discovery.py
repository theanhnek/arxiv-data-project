"""Resolve assigned arXiv IDs (or assigned month/ID ranges) and enumerate all source versions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

import requests

from .config import CrawlConfig

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
PATTERN = re.compile(
    r"(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7}))(?:v[1-9]\d*)?",
    re.IGNORECASE,
)
YEAR_MONTH_PATTERN = re.compile(
    r"^(?:(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])|(?P<yymm>\d{4}))$"
)
DEFAULT_MEMBER_QUOTA = 5000


def normalize_id(value: str) -> str:
    """Validate an arXiv identifier and return its canonical unversioned base ID."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid arXiv ID: {value!r}")
    cleaned = value.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[6:].strip()
    match = PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(f"Invalid arXiv ID: {value!r}")
    base = match["base"]
    if "/" in base:
        archive, number = base.split("/", 1)
        base = f"{archive.lower()}/{number}"
    return base


def parse_year_month(month_str: str) -> Tuple[int, int, str]:
    """Parse ``'YYYY-MM'`` or ``'YYMM'`` into ``(year, month, yymm_str)``."""
    if not isinstance(month_str, str):
        raise ValueError(f"Invalid month format: {month_str!r}")
    cleaned = month_str.strip()
    match = YEAR_MONTH_PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(
            f"Expected month in 'YYYY-MM' or 'YYMM' format, got: {month_str!r}"
        )
    if match.group("yymm"):
        yymm = match.group("yymm")
        year = 2000 + int(yymm[:2])
        month = int(yymm[2:])
        if not (1 <= month <= 12):
            raise ValueError(f"Invalid month in YYMM string: {month_str!r}")
        return year, month, yymm
    year = int(match.group("year"))
    month = int(match.group("month"))
    yymm = f"{year % 100:02d}{month:02d}"
    return year, month, yymm


def format_arxiv_id(month: str, paper_id: int) -> str:
    """Format a ``(month, paper_id)`` pair into a canonical arXiv ID.

    Examples:
        ``format_arxiv_id("2025-10", 9401)`` -> ``"2510.09401"``
        ``format_arxiv_id("2025-09", 26047)`` -> ``"2509.26047"``
    """
    if not isinstance(paper_id, int) or isinstance(paper_id, bool) or paper_id < 1:
        raise ValueError(f"paper_id must be a positive integer, got: {paper_id!r}")
    year, month_num, yymm = parse_year_month(month)
    digits = 5 if (year > 2014 or (year == 2015 and month_num >= 1)) else 4
    if paper_id >= 10**digits and digits == 4:
        digits = 5
    if paper_id >= 100000:
        raise ValueError(f"paper_id exceeds 5-digit arXiv numbering: {paper_id!r}")
    return f"{yymm}.{paper_id:0{digits}d}"


def parse_assigned_range_line(line: str) -> Dict[str, Any]:
    """Parse one assigned range row ``start_month start_id end_month end_id``.

    Supports tab-separated, whitespace-separated, or ``' - '`` separated fields.
    """
    if not isinstance(line, str):
        raise ValueError(f"Invalid range line: {line!r}")
    stripped = line.strip()
    if "\t" in stripped:
        parts = [p.strip() for p in stripped.split("\t") if p.strip()]
    else:
        normalized = re.sub(r"\s+-\s+", " ", stripped)
        parts = normalized.split()
    if len(parts) != 4:
        raise ValueError(
            f"Expected 4 columns (start_month, start_id, end_month, end_id), got: {line!r}"
        )
    start_month_raw, start_id_raw, end_month_raw, end_id_raw = parts
    start_year, start_m, _ = parse_year_month(start_month_raw)
    end_year, end_m, _ = parse_year_month(end_month_raw)
    try:
        start_id = int(start_id_raw)
        end_id = int(end_id_raw)
    except ValueError as exc:
        raise ValueError(f"start_id and end_id must be integers in {line!r}") from exc

    if start_id < 1 or end_id < 1:
        raise ValueError(f"start_id and end_id must be >= 1 in {line!r}")
    if (start_year, start_m) > (end_year, end_m):
        raise ValueError(f"start_month is after end_month in {line!r}")
    if (start_year, start_m) == (end_year, end_m) and start_id > end_id:
        raise ValueError(f"start_id > end_id within same month in {line!r}")

    return {
        "start_month": f"{start_year:04d}-{start_m:02d}",
        "start_id": start_id,
        "end_month": f"{end_year:04d}-{end_m:02d}",
        "end_id": end_id,
    }


def _iter_months(start_month: str, end_month: str) -> List[str]:
    sy, sm, _ = parse_year_month(start_month)
    ey, em, _ = parse_year_month(end_month)
    if (sy, sm) > (ey, em):
        raise ValueError(f"start_month {start_month!r} > end_month {end_month!r}")
    months: List[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return months


def expand_assigned_range(
    start_month: str,
    start_id: int,
    end_month: str,
    end_id: int,
    *,
    month_max_ids: Optional[Dict[str, int]] = None,
    expected_total: Optional[int] = DEFAULT_MEMBER_QUOTA,
) -> List[str]:
    """Expand an assigned ``(start_month, start_id, end_month, end_id)`` into arXiv IDs.

    - Same-month range (e.g. ``2025-10 9401 2025-10 14400``):
      expands directly to ``["2510.09401", ..., "2510.14400"]``.
    - Cross-month range (e.g. ``2025-09 26047 2025-10 4400``):
      uses ``month_max_ids`` (such as ``{"2025-09": 26646}``) or ``expected_total``
      (defaults to ``5000`` papers per member) to determine the upper bound of ``start_month``.
    """
    months = _iter_months(start_month, end_month)
    if len(months) == 1:
        if start_id > end_id:
            raise ValueError(f"start_id ({start_id}) > end_id ({end_id}) for {start_month}")
        return [format_arxiv_id(months[0], num) for num in range(start_id, end_id + 1)]

    normalized_max: Dict[str, int] = {}
    if month_max_ids:
        for k, v in month_max_ids.items():
            ky, km, _ = parse_year_month(k)
            normalized_max[f"{ky:04d}-{km:02d}"] = int(v)

    if len(months) == 2 and months[0] not in normalized_max and expected_total is not None:
        first_month_count = expected_total - end_id
        if first_month_count < 1:
            raise ValueError(
                f"expected_total={expected_total} is smaller than end_id={end_id}"
            )
        normalized_max[months[0]] = start_id + first_month_count - 1

    ids: List[str] = []
    for idx, month in enumerate(months):
        first_num = start_id if idx == 0 else 1
        if idx == len(months) - 1:
            last_num = end_id
        else:
            if month not in normalized_max:
                raise ValueError(
                    f"Cross-month range requires month_max_ids[{month!r}] or expected_total"
                )
            last_num = normalized_max[month]
        if first_num > last_num:
            raise ValueError(
                f"Invalid ID bounds {first_num}..{last_num} for month {month}"
            )
        ids.extend(format_arxiv_id(month, num) for num in range(first_num, last_num + 1))
    return ids


def load_assigned_ranges(path: Path) -> List[Dict[str, Any]]:
    """Read an assigned-ranges TSV/text file and return structured range dicts."""
    ranges: List[Dict[str, Any]] = []
    for line_no, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("start_month"):
            continue
        try:
            entry = parse_assigned_range_line(stripped)
            entry["line"] = line_no
            ranges.append(entry)
        except ValueError as exc:
            raise ValueError(f"Line {line_no}: {exc}") from exc
    return ranges


def prepare_assigned_ids(
    start_month: str,
    start_id: int,
    end_month: str,
    end_id: int,
    output_path: Path,
    *,
    month_max_ids: Optional[Dict[str, int]] = None,
    expected_total: Optional[int] = DEFAULT_MEMBER_QUOTA,
) -> List[str]:
    """Expand an assigned range and write ``data/assigned_ids.txt`` for Discovery."""
    ids = expand_assigned_range(
        start_month,
        start_id,
        end_month,
        end_id,
        month_max_ids=month_max_ids,
        expected_total=expected_total,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Assigned range: {start_month} {start_id} -> {end_month} {end_id} "
        f"({len(ids)} IDs)\n"
    )
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(header + "\n".join(ids) + "\n", encoding="utf-8")
    temp_path.replace(output_path)
    return ids


def load_ids(
    path: Path,
    *,
    member_index: Optional[int] = None,
    expected_total: Optional[int] = DEFAULT_MEMBER_QUOTA,
    month_max_ids: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Load and validate arXiv IDs from a text file.

    Supports both:
    - Single arXiv IDs per line (``2310.12345``, ``hep-th/9901001``, ``2310.12345v2``)
    - Assigned range rows (``start_month start_id end_month end_id``, e.g.
      ``2025-10\\t9401\\t2025-10\\t14400``). When ``member_index`` (1-based) is
      given, only that member's range row is expanded.
    """
    ids: List[str] = []
    seen = set()
    invalid: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    range_ordinal = 0

    for number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.lower().startswith("start_month"):
            continue

        # First, check if the line is an assigned range row (4 tokens)
        if "\t" in line or len(re.sub(r"\s+-\s+", " ", line).split()) == 4:
            try:
                parsed_range = parse_assigned_range_line(line)
                range_ordinal += 1
                if member_index is not None and range_ordinal != member_index:
                    continue
                expanded = expand_assigned_range(
                    parsed_range["start_month"],
                    parsed_range["start_id"],
                    parsed_range["end_month"],
                    parsed_range["end_id"],
                    month_max_ids=month_max_ids,
                    expected_total=expected_total,
                )
                for base in expanded:
                    if base in seen:
                        duplicates.append(
                            {"line": number, "input": line, "arxiv_id": base}
                        )
                    else:
                        seen.add(base)
                        ids.append(base)
                continue
            except ValueError as exc:
                invalid.append({"line": number, "input": line, "error": str(exc)})
                continue

        try:
            base = normalize_id(line)
        except ValueError as exc:
            invalid.append({"line": number, "input": line, "error": str(exc)})
            continue
        if base in seen:
            duplicates.append({"line": number, "input": line, "arxiv_id": base})
        else:
            seen.add(base)
            ids.append(base)
    return {"ids": ids, "invalid": invalid, "duplicates": duplicates}


def parse_paper(content: bytes, expected: str) -> Dict[str, Any]:
    root = ET.fromstring(content)
    expected_base = normalize_id(expected)
    for entry in root.findall("a:entry", NS):
        identifier = entry.findtext("a:id", "", NS)
        if identifier.endswith("/errors"):
            raise ValueError(entry.findtext("a:summary", "API error", NS))
        identifier = identifier.split("/abs/")[-1]
        try:
            base = normalize_id(identifier)
        except ValueError:
            continue
        if base != expected_base:
            continue
        version = re.search(r"v([1-9]\d*)$", identifier)
        if not version:
            raise ValueError("Missing latest-version suffix")
        latest = int(version[1])

        def text(field: str) -> str:
            return " ".join(entry.findtext(field, "", NS).split())

        return {
            "arxiv_id": base,
            "title": text("a:title"),
            "authors": [
                " ".join(a.findtext("a:name", "", NS).split())
                for a in entry.findall("a:author", NS)
            ],
            "abstract": text("a:summary"),
            "submission_date": text("a:published") or None,
            "latest_updated_date": text("a:updated") or None,
            "revised_dates": [],
            "revision_dates_complete": latest == 1,
            "venue": text("x:journal_ref") or None,
            "doi": text("x:doi") or None,
            "latest_version": latest,
            "versions": [
                {
                    "version": n,
                    "version_id": f"{base}v{n}",
                    "source_url": f"https://export.arxiv.org/src/{base}v{n}",
                    "download_status": "pending",
                    "extraction_status": "pending",
                }
                for n in range(1, latest + 1)
            ],
            "metadata_source": API,
        }
    raise LookupError(f"No matching entry for {expected_base}")


class ArxivClient:
    """Sequential requests; reuse this client when integrating downloader."""

    def __init__(self, config: CrawlConfig):
        if (
            config.arxiv_delay_seconds < 3
            or config.max_attempts < 1
            or config.request_timeout_seconds <= 0
        ):
            raise ValueError("Require delay >= 3s, attempts >= 1, timeout > 0")
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.user_agent
        self.last_request: Optional[float] = None

    def fetch(self, base: str) -> bytes:
        for attempt in range(self.config.max_attempts):
            if self.last_request is not None:
                time.sleep(
                    max(
                        0.0,
                        self.config.arxiv_delay_seconds
                        - (time.monotonic() - self.last_request),
                    )
                )
            self.last_request = time.monotonic()
            try:
                response = self.session.get(
                    API,
                    params={"id_list": base, "max_results": 1},
                    timeout=self.config.request_timeout_seconds,
                )
                self.last_request = time.monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 == self.config.max_attempts:
                        response.raise_for_status()
                    delay = 3 * 2**attempt
                    try:
                        delay = max(
                            delay, float(response.headers.get("Retry-After", "0"))
                        )
                    except ValueError:
                        pass
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.content
            except (requests.Timeout, requests.ConnectionError):
                self.last_request = time.monotonic()
                if attempt + 1 == self.config.max_attempts:
                    raise
                time.sleep(3 * 2**attempt)
        raise RuntimeError("Attempts exhausted")

    def close(self) -> None:
        self.session.close()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def discover_papers(
    ids: Iterable[str],
    config: CrawlConfig,
    *,
    resume: bool = False,
    client: Optional[ArxivClient] = None,
) -> Dict[str, Any]:
    bases = list(dict.fromkeys(normalize_id(value) for value in ids))
    path = config.output_dir / "state" / "discovery.json"
    cached: Dict[str, Dict[str, Any]] = {}
    if resume and path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        cached = {
            p["arxiv_id"]: p
            for p in previous.get("papers", [])
            if p.get("status") == "resolved"
        }
    started = time.monotonic()
    report: Dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "papers": [],
    }
    owned = client is None
    client = client or ArxivClient(config)
    try:
        total_bases = len(bases)
        for idx, base in enumerate(bases, 1):
            paper_start = time.monotonic()
            if base in cached:
                record = dict(cached[base], cache_hit=True)
                print(f"[Discovery {idx}/{total_bases}] {base}: cached ({record.get('latest_version', 1)} versions)")
            else:
                try:
                    record = dict(
                        parse_paper(client.fetch(base), base),
                        status="resolved",
                        cache_hit=False,
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                    )
                    print(f"[Discovery {idx}/{total_bases}] {base}: resolved ({record['latest_version']} versions)")
                except (
                    requests.RequestException,
                    ET.ParseError,
                    ValueError,
                    LookupError,
                ) as exc:
                    record = {
                        "arxiv_id": base,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "cache_hit": False,
                    }
                    print(f"[Discovery {idx}/{total_bases}] {base}: {type(exc).__name__}")
            record["discovery_seconds"] = time.monotonic() - paper_start
            report["papers"].append(record)
            report["elapsed_seconds"] = time.monotonic() - started
            write_json(path, report)
    finally:
        if owned:
            client.close()
    resolved = [p for p in report["papers"] if p["status"] == "resolved"]
    report["summary"] = {
        "assigned_unique_ids": len(bases),
        "resolved": len(resolved),
        "failed": len(bases) - len(resolved),
        "expected_versions": sum(p["latest_version"] for p in resolved),
        "cache_hits": sum(1 for p in report["papers"] if p["cache_hit"]),
    }
    report["elapsed_seconds"] = time.monotonic() - started
    write_json(path, report)
    return report


def discover_assigned_range(
    start_month: str,
    start_id: int,
    end_month: str,
    end_id: int,
    config: CrawlConfig,
    *,
    resume: bool = False,
    client: Optional[ArxivClient] = None,
    month_max_ids: Optional[Dict[str, int]] = None,
    expected_total: Optional[int] = DEFAULT_MEMBER_QUOTA,
) -> Dict[str, Any]:
    """Expand an assigned range into IDs, save ``assigned_ids.txt``, and run Discovery."""
    ids_path = config.output_dir / "assigned_ids.txt"
    ids = prepare_assigned_ids(
        start_month,
        start_id,
        end_month,
        end_id,
        ids_path,
        month_max_ids=month_max_ids,
        expected_total=expected_total,
    )
    write_json(
        config.output_dir / "state" / "input_validation.json",
        {
            "assigned_range": {
                "start_month": start_month,
                "start_id": start_id,
                "end_month": end_month,
                "end_id": end_id,
            },
            "ids": ids,
            "invalid": [],
            "duplicates": [],
        },
    )
    return discover_papers(ids, config, resume=resume, client=client)
