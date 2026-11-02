import io
import json
from pathlib import Path
import pytest
import tarfile

from src.config import CrawlConfig
from src.extractor import (
    detect_source_format,
    extract_downloaded_sources,
    extract_source,
)


def _write_tar(path: Path, members):
    with tarfile.open(path, mode="w:gz") as archive:
        for name, payload in members:
            if isinstance(payload, tarfile.TarInfo):
                archive.addfile(payload)
                continue
            data = payload
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_extracts_only_tex_and_bib_and_preserves_bytes(tmp_path):
    archive = tmp_path / "paper.tar.gz"
    tex = b"% unusual encoding preserved\n\\documentclass{article}\n"
    bib = b"@article{key, title={Example}}\n"
    _write_tar(
        archive,
        [
            ("paper/main.tex", tex),
            ("paper/refs.bib", bib),
            ("paper/figure.png", b"image bytes"),
        ],
    )

    destination = tmp_path / "source"
    result = extract_source(archive, destination)

    assert result.status == "success"
    assert result.input_bytes == archive.stat().st_size
    assert set(result.files) == {"paper/main.tex", "paper/refs.bib"}
    assert result.retained_bytes == len(tex) + len(bib)
    assert (destination / "paper/main.tex").read_bytes() == tex
    assert (destination / "paper/refs.bib").read_bytes() == bib
    assert not (destination / "paper/figure.png").exists()


def test_plain_tex_source_is_copied_without_modification(tmp_path):
    source = tmp_path / "single.tex"
    content = b"\\documentclass{article}\r\n\\begin{document}\r\n"
    source.write_bytes(content)

    result = extract_source(source, tmp_path / "source")

    assert result.status == "success"
    assert result.input_bytes == source.stat().st_size
    assert result.files == ["single.tex"]
    assert result.retained_bytes == len(content)
    assert (tmp_path / "source/single.tex").read_bytes() == content


def test_bib_only_archive_keeps_bib_and_reports_no_tex(tmp_path):
    archive = tmp_path / "bib-only.tar.gz"
    bib = b"@book{key, title={Example}}\n"
    _write_tar(archive, [("refs.bib", bib)])

    destination = tmp_path / "source"
    result = extract_source(archive, destination)

    assert result.status == "no_tex_source"
    assert result.files == ["refs.bib"]
    assert result.retained_bytes == len(bib)
    assert (destination / "refs.bib").read_bytes() == bib


def test_traversal_archive_is_rejected_without_writing_outside_destination(tmp_path):
    archive = tmp_path / "traversal.tar.gz"
    _write_tar(archive, [("../escape.tex", b"\\documentclass{article}")])
    destination = tmp_path / "source"

    result = extract_source(archive, destination)

    assert result.status == "unsafe_archive"
    assert not (tmp_path / "escape.tex").exists()
    assert list(destination.iterdir()) == []


def test_archive_links_are_rejected(tmp_path):
    archive = tmp_path / "link.tar.gz"
    link = tarfile.TarInfo("linked.tex")
    link.type = tarfile.SYMTYPE
    link.linkname = "outside.tex"
    _write_tar(archive, [("ignored", link)])

    result = extract_source(archive, tmp_path / "source")

    assert result.status == "unsafe_archive"


def test_corrupt_archive_is_distinguished_from_unsupported_format(tmp_path):
    corrupt = tmp_path / "broken.gz"
    corrupt.write_bytes(b"\x1f\x8bnot-a-valid-gzip")
    unsupported = tmp_path / "response.html"
    unsupported.write_text("<html>not a source archive</html>", encoding="utf-8")

    assert detect_source_format(corrupt) == "corrupt_archive"
    assert extract_source(corrupt, tmp_path / "broken-output").status == "corrupt_archive"
    assert extract_source(unsupported, tmp_path / "html-output").status == "unsupported_format"


def test_tar_without_tex_keeps_an_empty_source_record(tmp_path):
    archive = tmp_path / "no-tex.tar.gz"
    _write_tar(archive, [("figure.png", b"image only")])
    destination = tmp_path / "source"

    result = extract_source(archive, destination)

    assert result.status == "no_tex_source"
    assert result.files == []
    assert result.retained_bytes == 0
    assert list(destination.iterdir()) == []


def test_archive_uncompressed_bytes_counts_filtered_members(tmp_path):
    archive = tmp_path / "paper.tar.gz"
    members = [
        ("paper/main.tex", b"\\documentclass{article}\n"),
        ("paper/refs.bib", b"@article{key}\n"),
        ("paper/figure.png", b"image bytes"),
    ]
    _write_tar(archive, members)

    result = extract_source(archive, tmp_path / "source")

    assert result.status == "success"
    assert result.retained_bytes == len(members[0][1]) + len(members[1][1])
    assert result.archive_uncompressed_bytes == sum(
        len(payload) for _, payload in members
    )


def test_tex_only_archive_reports_success(tmp_path):
    archive = tmp_path / "tex-only.tar.gz"
    tex = b"\\documentclass{article}\n"
    _write_tar(archive, [("paper/main.tex", tex)])

    destination = tmp_path / "source"
    result = extract_source(archive, destination)

    assert result.status == "success"
    assert result.files == ["paper/main.tex"]
    assert result.retained_bytes == len(tex)
    assert result.archive_uncompressed_bytes == len(tex)
    assert (destination / "paper/main.tex").read_bytes() == tex


def test_nested_archive_paths_are_preserved(tmp_path):
    archive = tmp_path / "nested.tar.gz"
    tex = b"\\section{Introduction}\n"
    bib = b"@book{key}\n"
    _write_tar(
        archive,
        [
            ("paper/src/sections/introduction.tex", tex),
            ("paper/references/library.bib", bib),
        ],
    )

    destination = tmp_path / "source"
    result = extract_source(archive, destination)

    assert result.status == "success"
    assert set(result.files) == {
        "paper/src/sections/introduction.tex",
        "paper/references/library.bib",
    }
    assert (destination / "paper/src/sections/introduction.tex").read_bytes() == tex
    assert (destination / "paper/references/library.bib").read_bytes() == bib


def test_extract_downloaded_sources_batch_and_resume(tmp_path):
    config = CrawlConfig(output_dir=tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    paper_v1_dir = tmp_path / "corpus" / "2310-12345" / "v1"
    paper_v1_dir.mkdir(parents=True, exist_ok=True)
    archive_path = paper_v1_dir / "v1.tar.gz"
    tex_bytes = b"\\documentclass{article}\n"
    _write_tar(
        archive_path,
        [("main.tex", tex_bytes), ("fig.png", b"image-data")],
    )

    downloads_report = {
        "schema_version": 1,
        "versions": [
            {
                "version_id": "2310.12345v1",
                "arxiv_id": "2310.12345",
                "version": 1,
                "status": "downloaded",
                "source_path": str(archive_path),
                "bytes_downloaded": archive_path.stat().st_size,
            },
            {
                "version_id": "2310.12345v2",
                "arxiv_id": "2310.12345",
                "version": 2,
                "status": "not_found",
                "source_path": None,
                "bytes_downloaded": 0,
            },
        ],
    }
    downloads_path = state_dir / "downloads.json"
    downloads_path.write_text(json.dumps(downloads_report), encoding="utf-8")

    report = extract_downloaded_sources(config)

    assert report["summary"]["expected_versions"] == 2
    assert report["summary"]["success"] == 1
    assert report["summary"]["skipped_not_downloaded"] == 1
    assert report["summary"]["failed"] == 0
    assert report["summary"]["cache_hits"] == 0
    assert (paper_v1_dir / "source" / "main.tex").read_bytes() == tex_bytes
    assert not (paper_v1_dir / "source" / "fig.png").exists()
    assert (paper_v1_dir / "extraction_state.json").is_file()
    assert (state_dir / "extractions.json").is_file()

    # Test resume reuses cached extraction
    resumed = extract_downloaded_sources(config, resume=True)
    assert resumed["summary"]["cache_hits"] == 1


def test_extract_downloaded_sources_missing_downloads_raises(tmp_path):
    config = CrawlConfig(output_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="Downloads state file not found"):
        extract_downloaded_sources(config)
