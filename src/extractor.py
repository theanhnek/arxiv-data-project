"""Safely extract only TeX and BibTeX source files from paper submissions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tarfile
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .config import CrawlConfig
from .discovery import write_json
from .downloader import safe_paper_dirname


PathInput = Union[str, Path]


@dataclass
class ExtractionResult:
    """Summary of one source extraction attempt.

    ``files`` contains paths relative to the destination. ``retained_bytes``
    counts bytes written for allowed source files. ``input_bytes`` is the
    downloaded input size, while ``archive_uncompressed_bytes`` sums regular
    archive members before filtering.
    """

    status: str
    input_bytes: int = 0
    files: List[str] = field(default_factory=list)
    retained_bytes: int = 0
    error: Optional[str] = None
    archive_uncompressed_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "input_bytes": self.input_bytes,
            "files": list(self.files),
            "retained_bytes": self.retained_bytes,
            "error": self.error,
            "archive_uncompressed_bytes": self.archive_uncompressed_bytes,
        }


class _UnsafeArchiveError(Exception):
    """Raised when an archive contains a path or member we must reject."""


def _archive_signature(sample: bytes) -> bool:
    """Return whether bytes look like a supported tar compression format."""
    return (
        sample.startswith(b"\x1f\x8b")  # gzip
        or sample.startswith(b"BZh")  # bzip2
        or sample.startswith(b"\xfd7zXZ\x00")  # xz
        or sample[257:262] == b"ustar"  # tar
    )


def _looks_like_text_source(path: Path, sample: bytes) -> bool:
    """Recognize plain TeX/Bib files without relying only on the extension."""
    if b"\x00" in sample:
        return False

    text = sample.decode("utf-8", errors="ignore")
    tex_markers = (
        r"\documentclass",
        r"\begin{document}",
        r"\usepackage",
        r"\include{",
        r"\input{",
    )
    bib_markers = ("@article", "@book", "@inproceedings", "@misc", "@techreport")
    if path.suffix.lower() == ".tex":
        return any(marker in text for marker in tex_markers) or bool(text.strip())
    if path.suffix.lower() == ".bib":
        lowered = text.lower()
        return any(marker in lowered for marker in bib_markers) or bool(text.strip())
    return False


def detect_source_format(source_path: PathInput) -> str:
    """Detect a tar archive, plain TeX/Bib file, corrupt archive, or unknown input.

    The function inspects the content first. A ``.tex`` or ``.bib`` suffix is
    used only as a hint for plain-text source files.
    """
    path = Path(source_path)
    if not path.is_file():
        return "source_missing"

    try:
        with tarfile.open(path, mode="r:*") as archive:
            archive.getmembers()
        return "tar_archive"
    except tarfile.ReadError:
        pass
    except (tarfile.TarError, EOFError):
        # A recognized archive that cannot be read is reported separately below.
        pass
    except OSError:
        # Retry a small read below: this distinguishes permission/I/O problems
        # from damaged gzip/tar data whose signature is still recognizable.
        pass

    try:
        with path.open("rb") as source_file:
            sample = source_file.read(64 * 1024)
    except OSError:
        return "source_unreadable"

    if _archive_signature(sample):
        return "corrupt_archive"
    if _looks_like_text_source(path, sample):
        return "plain_source"
    return "unsupported_format"


def _safe_member_parts(member_name: str) -> Tuple[str, ...]:
    """Validate an archive path and return its safe relative path components."""
    if "\x00" in member_name:
        raise _UnsafeArchiveError("Archive member contains a NUL byte")

    # Treat backslashes as separators too, so Windows-style traversal is caught.
    normalized = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(member_name)
    drive, _ = ntpath.splitdrive(normalized)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or drive
        or any(part == ".." for part in posix_path.parts)
    ):
        raise _UnsafeArchiveError(f"Unsafe archive path: {member_name!r}")

    parts = tuple(part for part in posix_path.parts if part not in ("", "."))
    if not parts:
        raise _UnsafeArchiveError(f"Empty archive path: {member_name!r}")
    return parts


def _copy_stream(source, destination) -> int:
    """Copy a binary stream without changing bytes and return its byte count."""
    count = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        destination.write(chunk)
        count += len(chunk)
    return count


def _ensure_within(path: Path, root: Path) -> None:
    """Raise if a resolved path escapes ``root``."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise _UnsafeArchiveError(f"Output path escapes destination: {path}") from exc


def _install_staged_files(staging: Path, destination: Path, files: List[str]) -> None:
    """Move staged files into the destination after extraction succeeds."""
    root = destination.resolve()
    for relative_name in files:
        parts = PurePosixPath(relative_name).parts
        staged_file = staging.joinpath(*parts)
        output_file = destination.joinpath(*parts)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        _ensure_within(output_file.parent, root)
        os.replace(staged_file, output_file)


def _extract_tar(
    source_path: Path, staging: Path
) -> tuple[List[str], int, int, bool]:
    """Stage allowed files and measure every regular archive member."""
    extracted: List[str] = []
    retained_bytes = 0
    archive_uncompressed_bytes = 0
    seen = set()

    with tarfile.open(source_path, mode="r:*") as archive:
        for member in archive:
            parts = _safe_member_parts(member.name)

            if member.issym() or member.islnk():
                raise _UnsafeArchiveError(
                    f"Archive links are not allowed: {member.name!r}"
                )

            if member.isdir():
                continue
            if not member.isfile():
                continue
            archive_uncompressed_bytes += member.size

            if Path(parts[-1]).suffix.lower() not in (".tex", ".bib"):
                continue

            relative_name = PurePosixPath(*parts).as_posix()
            if relative_name in seen:
                raise _UnsafeArchiveError(
                    f"Duplicate source file in archive: {relative_name!r}"
                )
            seen.add(relative_name)

            staged_file = staging.joinpath(*parts)
            _ensure_within(staged_file, staging)
            staged_file.parent.mkdir(parents=True, exist_ok=True)

            member_stream = archive.extractfile(member)
            if member_stream is None:
                raise tarfile.ReadError(
                    f"Could not read archive member {member.name!r}"
                )
            with member_stream, staged_file.open("xb") as output:
                copied = _copy_stream(member_stream, output)

            if copied != member.size:
                raise tarfile.ReadError(
                    f"Truncated archive member {member.name!r}: "
                    f"expected {member.size} bytes, read {copied}"
                )

            extracted.append(relative_name)
            retained_bytes += copied

    return (
        extracted,
        retained_bytes,
        archive_uncompressed_bytes,
        any(PurePosixPath(name).suffix.lower() == ".tex" for name in extracted),
    )


def _extract_plain_source(
    source_path: Path, staging: Path
) -> tuple[List[str], int, int, bool]:
    """Stage one plain .tex or .bib source file without altering its bytes."""
    name = source_path.name
    if Path(name).suffix.lower() not in (".tex", ".bib"):
        return [], 0, 0, False

    staged_file = staging / name
    with source_path.open("rb") as source, staged_file.open("xb") as output:
        retained_bytes = _copy_stream(source, output)

    has_tex = Path(name).suffix.lower() == ".tex"
    return [name], retained_bytes, 0, has_tex


def extract_source(source_path: PathInput, destination: PathInput) -> ExtractionResult:
    """Extract only regular ``.tex``/``.bib`` files while preserving their bytes.

    Supported inputs are tar archives readable by :mod:`tarfile` and single
    plain-text ``.tex`` or ``.bib`` files. Other formats are reported instead
    of being silently treated as source. The destination is normally a version's
    ``source/`` directory.
    """
    source = Path(source_path)
    target = Path(destination)
    try:
        input_bytes = source.stat().st_size
    except OSError:
        input_bytes = 0

    source_format = detect_source_format(source)
    if source_format in ("source_missing", "source_unreadable"):
        return ExtractionResult(
            status=source_format, input_bytes=input_bytes, error=str(source)
        )
    if source_format == "unsupported_format":
        return ExtractionResult(
            status="unsupported_format",
            input_bytes=input_bytes,
            error="Input is neither a readable tar archive nor a plain .tex/.bib file",
        )
    if source_format == "corrupt_archive":
        return ExtractionResult(
            status="corrupt_archive",
            input_bytes=input_bytes,
            error="Archive signature found but it cannot be read",
        )

    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=target) as temp_name:
            staging = Path(temp_name)
            if source_format == "tar_archive":
                (
                    files,
                    retained_bytes,
                    archive_uncompressed_bytes,
                    has_tex,
                ) = _extract_tar(source, staging)
            else:
                (
                    files,
                    retained_bytes,
                    archive_uncompressed_bytes,
                    has_tex,
                ) = _extract_plain_source(source, staging)

            _install_staged_files(staging, target, files)

        return ExtractionResult(
            status="success" if has_tex else "no_tex_source",
            input_bytes=input_bytes,
            files=files,
            retained_bytes=retained_bytes,
            archive_uncompressed_bytes=archive_uncompressed_bytes,
        )
    except _UnsafeArchiveError as exc:
        return ExtractionResult(
            status="unsafe_archive", input_bytes=input_bytes, error=str(exc)
        )
    except (tarfile.TarError, EOFError) as exc:
        return ExtractionResult(
            status="corrupt_archive", input_bytes=input_bytes, error=str(exc)
        )
    except OSError as exc:
        return ExtractionResult(
            status="extraction_error", input_bytes=input_bytes, error=str(exc)
        )


def extract_downloaded_sources(
    config: CrawlConfig,
    *,
    downloads_path: Optional[Path] = None,
    resume: bool = False,
) -> Dict[str, Any]:
    """Extract all successfully downloaded sources recorded in ``state/downloads.json``.

    Requires that Downloader has run and produced ``state/downloads.json``.
    Extracts regular ``.tex``/``.bib`` files into ``source/`` under each version's
    directory in ``corpus/<safe_id>/v<N>/source/``, records individual version
    extraction states, and writes an atomic overall report to ``state/extractions.json``.
    """
    downloads_file = downloads_path or (config.output_dir / "state" / "downloads.json")
    if not downloads_file.is_file():
        raise FileNotFoundError(
            f"Downloads state file not found: {downloads_file}. "
            "Run download first before extracting sources."
        )

    downloads_report = json.loads(downloads_file.read_text(encoding="utf-8"))
    report_path = config.output_dir / "state" / "extractions.json"
    started = time.monotonic()

    cached: Dict[str, Dict[str, Any]] = {}
    if resume and report_path.is_file():
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
            cached = {
                item["version_id"]: item
                for item in previous.get("versions", [])
                if item.get("status") in ("success", "no_tex_source")
            }
        except (json.JSONDecodeError, OSError):
            cached = {}

    report: Dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "downloads_path": str(downloads_file),
        "versions": [],
    }

    versions_list = downloads_report.get("versions", [])
    total_ext = len(versions_list)
    for v_entry_idx, v_entry in enumerate(versions_list, 1):
        version_id = v_entry["version_id"]
        arxiv_id = v_entry.get("arxiv_id", "")
        version_num = v_entry.get("version", 1)
        dl_status = v_entry.get("status")

        if dl_status != "downloaded" or not v_entry.get("source_path"):
            report["versions"].append(
                {
                    "version_id": version_id,
                    "arxiv_id": arxiv_id,
                    "version": version_num,
                    "source_path": None,
                    "destination": None,
                    "status": "skipped_not_downloaded",
                    "input_bytes": 0,
                    "files": [],
                    "retained_bytes": 0,
                    "archive_uncompressed_bytes": 0,
                    "error": f"Source was not downloaded (download status: {dl_status})",
                    "cache_hit": False,
                    "extracted_at": None,
                }
            )
            continue

        raw_source_path = Path(v_entry["source_path"])
        if raw_source_path.is_absolute():
            source_path = raw_source_path
        else:
            # Check relative to repo root first, then config.output_dir
            if raw_source_path.exists():
                source_path = raw_source_path.resolve()
            elif (config.output_dir.parent / raw_source_path).exists():
                source_path = (config.output_dir.parent / raw_source_path).resolve()
            elif (config.output_dir / raw_source_path).exists():
                source_path = (config.output_dir / raw_source_path).resolve()
            else:
                source_path = raw_source_path

        if arxiv_id:
            version_dir = (
                config.output_dir
                / "corpus"
                / safe_paper_dirname(arxiv_id)
                / f"v{version_num}"
            )
        else:
            version_dir = source_path.parent
        destination = version_dir / "source"
        state_file = version_dir / "extraction_state.json"

        if resume and version_id in cached:
            cached_record = cached[version_id]
            if destination.is_dir() or cached_record.get("status") == "no_tex_source":
                record = dict(cached_record, cache_hit=True)
                print(f"[Extract {v_entry_idx}/{total_ext}] {version_id}: cached ({len(record.get('files', []))} files)")
                report["versions"].append(record)
                report["elapsed_seconds"] = time.monotonic() - started
                write_json(report_path, report)
                continue

        result = extract_source(source_path, destination)
        extracted_at = datetime.now(timezone.utc).isoformat()
        record = {
            "version_id": version_id,
            "arxiv_id": arxiv_id,
            "version": version_num,
            "source_path": str(source_path),
            "destination": str(destination),
            "status": result.status,
            "input_bytes": result.input_bytes,
            "files": result.files,
            "retained_bytes": result.retained_bytes,
            "archive_uncompressed_bytes": result.archive_uncompressed_bytes,
            "error": result.error,
            "cache_hit": False,
            "extracted_at": extracted_at,
        }
        print(
            f"[Extract {v_entry_idx}/{total_ext}] {version_id}: {result.status} "
            f"({len(result.files)} files, {result.retained_bytes} bytes)"
        )
        write_json(state_file, record)
        report["versions"].append(record)
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(report_path, report)

    versions = report["versions"]
    report["summary"] = {
        "expected_versions": len(versions),
        "success": sum(1 for v in versions if v["status"] == "success"),
        "no_tex_source": sum(1 for v in versions if v["status"] == "no_tex_source"),
        "skipped_not_downloaded": sum(
            1 for v in versions if v["status"] == "skipped_not_downloaded"
        ),
        "failed": sum(
            1
            for v in versions
            if v["status"] not in ("success", "no_tex_source", "skipped_not_downloaded")
        ),
        "cache_hits": sum(1 for v in versions if v.get("cache_hit")),
        "total_input_bytes": sum(v.get("input_bytes", 0) for v in versions),
        "total_retained_bytes": sum(v.get("retained_bytes", 0) for v in versions),
        "total_archive_uncompressed_bytes": sum(
            v.get("archive_uncompressed_bytes", 0) for v in versions
        ),
    }
    report["elapsed_seconds"] = time.monotonic() - started
    write_json(report_path, report)
    return report


def main() -> None:
    """CLI for extracting TeX/BibTeX sources from data/state/downloads.json."""
    parser = argparse.ArgumentParser(
        description="Extract TeX/BibTeX sources from downloaded packages."
    )
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument(
        "--resume", action="store_true", help="Reuse previously successful extractions"
    )
    args = parser.parse_args()

    config = CrawlConfig(output_dir=args.output)
    try:
        report = extract_downloaded_sources(config, resume=args.resume)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    print(report["summary"])
    print(f"Extraction output: {args.output / 'state' / 'extractions.json'}")
    if report["summary"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
