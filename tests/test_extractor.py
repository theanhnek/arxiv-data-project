import io
import tarfile
from pathlib import Path

from src.extractor import detect_source_format, extract_source


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
