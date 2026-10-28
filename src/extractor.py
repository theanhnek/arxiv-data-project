"""Safely extract only TeX and BibTeX source files from paper submissions."""

from dataclasses import dataclass, field
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tarfile
import tempfile
from typing import List, Optional, Tuple, Union


PathInput = Union[str, Path]


@dataclass
class ExtractionResult:
    """Summary of one source extraction attempt.

    ``files`` contains paths relative to the destination. ``retained_bytes``
    counts the uncompressed bytes written for those files. ``input_bytes`` is
    the size of the source file presented to the extractor.
    """

    status: str
    input_bytes: int = 0
    files: List[str] = field(default_factory=list)
    retained_bytes: int = 0
    error: Optional[str] = None


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
) -> tuple[List[str], int, bool]:
    """Extract allowed regular files from a validated tar archive."""
    extracted: List[str] = []
    retained_bytes = 0
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

    return extracted, retained_bytes, any(
        PurePosixPath(name).suffix.lower() == ".tex" for name in extracted
    )


def _extract_plain_source(
    source_path: Path, staging: Path
) -> tuple[List[str], int, bool]:
    """Stage one plain .tex or .bib source file without altering its bytes."""
    name = source_path.name
    if Path(name).suffix.lower() not in (".tex", ".bib"):
        return [], 0, False

    staged_file = staging / name
    with source_path.open("rb") as source, staged_file.open("xb") as output:
        retained_bytes = _copy_stream(source, output)

    has_tex = Path(name).suffix.lower() == ".tex"
    return [name], retained_bytes, has_tex


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
                files, retained_bytes, has_tex = _extract_tar(source, staging)
            else:
                files, retained_bytes, has_tex = _extract_plain_source(source, staging)

            _install_staged_files(staging, target, files)

        return ExtractionResult(
            status="success" if has_tex else "no_tex_source",
            input_bytes=input_bytes,
            files=files,
            retained_bytes=retained_bytes,
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
