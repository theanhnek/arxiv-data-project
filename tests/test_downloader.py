import hashlib
import io
from pathlib import Path
import tarfile
from unittest.mock import Mock, patch

import requests

from src.config import CrawlConfig
from src.discovery import ArxivClient, discover_papers, load_ids, write_json
from src.downloader import (
    _reset_pacing_for_tests,
    build_source_url,
    discover_and_download,
    download_discovered_sources,
    download_source,
    expand_assigned_range,
    format_arxiv_id,
    load_assigned_ranges,
    parse_assigned_range_line,
    parse_version_id,
    prepare_assigned_ids,
    safe_paper_dirname,
)
from src.extractor import extract_source


def _make_tar_gz_bytes(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _atom_feed(base="2510.09401", latest=2):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>http://arxiv.org/abs/{base}v{latest}</id>
    <title>Test Paper {base}</title><author><name>Bob</name></author>
    <published>2025-10-10T00:00:00Z</published>
    <updated>2025-10-12T00:00:00Z</updated></entry></feed>""".encode("utf-8")


def _empty_feed():
    return b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def _mock_response(status_code=200, payload=b"", headers=None):
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    chunks = [payload[i : i + 16] for i in range(0, len(payload), 16)] or [b""]
    response.iter_content.side_effect = lambda chunk_size=65536: iter(
        [c for c in chunks if c]
    )
    return response


def test_parse_version_id_and_safe_dirname():
    assert parse_version_id("2310.12345v2") == ("2310.12345", 2, "2310.12345v2")
    assert parse_version_id("arXiv:HEP-TH/9901001v1") == (
        "hep-th/9901001",
        1,
        "hep-th/9901001v1",
    )
    assert safe_paper_dirname("2310.12345") == "2310-12345"
    assert safe_paper_dirname("hep-th/9901001v3") == "hep-th-9901001"
    assert (
        build_source_url("2310.12345v1")
        == "https://export.arxiv.org/src/2310.12345v1"
    )

    for invalid in ("2310.12345", "2310.12345v0", "bad-id"):
        try:
            parse_version_id(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for {invalid!r}")


def test_assigned_ranges_prepare_ids_for_discovery(tmp_path):
    sample_table = (
        "2025-10\t14401\t2025-10\t19400\n"
        "2025-10\t9401\t2025-10\t14400\n"
        "2025-10\t4401\t2025-10\t9400\n"
        "2025-09\t26047\t2025-10\t4400\n"
        "2025-09\t11047\t2025-09\t16046\n"
    )
    range_file = tmp_path / "assigned_ranges.tsv"
    range_file.write_text(sample_table, encoding="utf-8")

    ranges = load_assigned_ranges(range_file)
    assert len(ranges) == 5

    # Member 2's range: 2025-10 9401 -> 2025-10 14400 (2510.09401 .. 2510.14400)
    m2 = ranges[1]
    ids_file = tmp_path / "assigned_ids.txt"
    m2_ids = prepare_assigned_ids(
        m2["start_month"], m2["start_id"], m2["end_month"], m2["end_id"], ids_file
    )
    assert len(m2_ids) == 5000
    assert m2_ids[0] == "2510.09401"
    assert m2_ids[-1] == "2510.14400"

    # Verify the generated assigned_ids.txt is accepted cleanly by Member 1's load_ids()
    loaded = load_ids(ids_file)
    assert loaded["ids"] == m2_ids
    assert loaded["invalid"] == []
    assert loaded["duplicates"] == []

    # Cross-month range (Row 4: 2025-09 26047 -> 2025-10 4400) with default 5000 quota
    m4 = ranges[3]
    m4_ids = expand_assigned_range(
        m4["start_month"],
        m4["start_id"],
        m4["end_month"],
        m4["end_id"],
        expected_total=5000,
    )
    assert len(m4_ids) == 5000
    assert m4_ids[0] == "2509.26047"
    assert m4_ids[599] == "2509.26646"
    assert m4_ids[600] == "2510.00001"
    assert m4_ids[-1] == "2510.04400"

    parsed_hyphen = parse_assigned_range_line("2025-10 - 9401 - 2025-10 - 14400")
    assert format_arxiv_id(parsed_hyphen["start_month"], parsed_hyphen["start_id"]) == "2510.09401"


def test_discover_first_then_download_workflow(tmp_path):
    _reset_pacing_for_tests()
    config = CrawlConfig(output_dir=tmp_path)

    # Prepare 2 IDs from Member 2's range (2510.09401 and 2510.09402)
    ids_path = tmp_path / "assigned_ids.txt"
    ids = prepare_assigned_ids("2025-10", 9401, "2025-10", 9402, ids_path)
    assert ids == ["2510.09401", "2510.09402"]

    payload_v1 = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}\n% v1")])
    payload_v2 = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}\n% v2")])

    client = Mock()
    client.last_request = None
    # 2510.09401 has 2 versions (v1, v2); 2510.09402 is not found on arXiv API
    client.fetch.side_effect = [
        _atom_feed("2510.09401", latest=2),
        _empty_feed(),
    ]
    client.session = Mock(spec=requests.Session)
    client.session.headers = {}
    client.session.get.side_effect = [
        _mock_response(200, payload_v1, {"Content-Type": "application/gzip"}),
        _mock_response(200, payload_v2, {"Content-Type": "application/gzip"}),
    ]

    with patch("src.downloader.time.sleep"):
        combined = discover_and_download(ids, config, client=client)

    # Verify Step 1 (Discovery) ran on both IDs first and wrote state/discovery.json
    assert client.fetch.call_count == 2
    assert (tmp_path / "state" / "discovery.json").is_file()
    assert combined["discovery"]["summary"]["assigned_unique_ids"] == 2
    assert combined["discovery"]["summary"]["resolved"] == 1
    assert combined["discovery"]["summary"]["expected_versions"] == 2

    # Verify Step 2 (Download) only downloaded the 2 versions identified by Discovery
    assert client.session.get.call_count == 2
    assert combined["downloads"]["summary"]["expected_versions"] == 2
    assert combined["downloads"]["summary"]["downloaded"] == 2
    assert [v["version_id"] for v in combined["downloads"]["versions"]] == [
        "2510.09401v1",
        "2510.09401v2",
    ]


def test_download_tar_gz_and_extract_integration(tmp_path):
    _reset_pacing_for_tests()
    tex_bytes = b"\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
    payload = _make_tar_gz_bytes(
        [("main.tex", tex_bytes), ("fig.png", b"binary-image")]
    )

    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = _mock_response(
        200, payload, {"Content-Type": "application/x-eprint-tar"}
    )

    config = CrawlConfig(output_dir=tmp_path, user_agent="TestCrawler/1.0 (test@example.com)")
    result = download_source("2310.12345v1", config, client=session)

    assert result.status == "downloaded"
    assert result["status"] == "downloaded"
    assert result.bytes_downloaded == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.detected_format == "tar_archive"
    assert result.source_path is not None and result.source_path.name == "v1.tar.gz"
    assert not (result.source_path.parent / "v1.part").exists()
    assert session.headers["User-Agent"] == "TestCrawler/1.0 (test@example.com)"

    # Verify compatibility with Member 3's extractor
    extraction = extract_source(result.source_path, tmp_path / "corpus/2310-12345/v1/source")
    assert extraction.status == "success"
    assert extraction.files == ["main.tex"]
    assert extraction.retained_bytes == len(tex_bytes)


def test_download_plain_tex_gets_tex_extension_for_extractor(tmp_path):
    _reset_pacing_for_tests()
    tex_bytes = b"\\documentclass{article}\n\\begin{document}\nSingle file\n\\end{document}\n"
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = _mock_response(
        200, tex_bytes, {"Content-Type": "application/x-tex"}
    )

    config = CrawlConfig(output_dir=tmp_path)
    result = download_source("hep-th/9901001v1", config, client=session)

    assert result.status == "downloaded"
    assert result.detected_format == "plain_tex"
    assert result.source_path is not None and result.source_path.suffix == ".tex"

    extraction = extract_source(result.source_path, tmp_path / "corpus/hep-th-9901001/v1/source")
    assert extraction.status == "success"
    assert extraction.retained_bytes == len(tex_bytes)


def test_html_error_and_throttling_page_never_saved_as_valid_source(tmp_path):
    _reset_pacing_for_tests()
    html_throttle = b"<!DOCTYPE html><html><body>Rate limit exceeded. Try again later.</body></html>"
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = [
        _mock_response(200, html_throttle, {"Content-Type": "text/html; charset=utf-8"}),
        _mock_response(200, html_throttle, {"Content-Type": "text/html; charset=utf-8"}),
    ]

    config = CrawlConfig(output_dir=tmp_path, max_attempts=2)
    with patch("src.downloader.time.sleep"):
        result = download_source("2310.12345v1", config, client=session)

    assert result.status == "invalid_response"
    assert result.error_type == "HTMLResponseError"
    assert result.attempts == 2
    assert result.source_path is None
    assert list((tmp_path / "tmp/downloads/2310-12345").glob("*")) == []


def test_retry_on_429_and_no_infinite_retry_on_404(tmp_path):
    _reset_pacing_for_tests()
    payload = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}")])
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = [
        _mock_response(429, b"Too Many Requests", {"Retry-After": "7"}),
        _mock_response(200, payload, {"Content-Type": "application/gzip"}),
    ]

    config = CrawlConfig(output_dir=tmp_path, max_attempts=3)
    with patch("src.downloader.time.sleep") as sleep_mock:
        ok = download_source("2310.12345v1", config, client=session)

    assert ok.status == "downloaded"
    assert ok.attempts == 2
    assert any(call.args and call.args[0] >= 7.0 for call in sleep_mock.call_args_list)

    # HTTP 404 must stop immediately after 1 attempt
    session_404 = Mock(spec=requests.Session)
    session_404.headers = {}
    session_404.get.return_value = _mock_response(404, b"Not Found")
    with patch("src.downloader.time.sleep"):
        missing = download_source("2310.99999v1", config, client=session_404)

    assert missing.status == "not_found"
    assert missing.attempts == 1
    assert session_404.get.call_count == 1


def test_resume_verifies_checksum_and_rejects_part_or_corrupt_files(tmp_path):
    _reset_pacing_for_tests()
    payload = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}")])
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = _mock_response(200, payload)

    config = CrawlConfig(output_dir=tmp_path)
    with patch("src.downloader.time.sleep"):
        first = download_source("2310.12345v1", config, client=session)
    assert first.status == "downloaded"
    assert not first.cache_hit

    # Resume when file + state + checksum are intact -> cache hit, 0 network requests
    session.get.reset_mock()
    resumed = download_source("2310.12345v1", config, resume=True, client=session)
    assert resumed.status == "downloaded"
    assert resumed.cache_hit is True
    assert resumed.attempts == 0
    session.get.assert_not_called()

    # Corrupt the downloaded file -> resume must detect checksum mismatch and re-download
    assert first.source_path is not None
    first.source_path.write_bytes(b"corrupted-bytes")
    session.get.return_value = _mock_response(200, payload)
    with patch("src.downloader.time.sleep"):
        redownloaded = download_source("2310.12345v1", config, resume=True, client=session)
    assert redownloaded.status == "downloaded"
    assert redownloaded.cache_hit is False
    assert redownloaded.attempts == 1
    session.get.assert_called_once()


def test_shared_pacing_with_discovery_arxiv_client(tmp_path):
    _reset_pacing_for_tests()
    config = CrawlConfig(output_dir=tmp_path, arxiv_delay_seconds=3.0)
    client = ArxivClient(config)
    client.last_request = 100.0
    payload = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}")])
    client.session = Mock(spec=requests.Session)
    client.session.headers = {}
    client.session.get.return_value = _mock_response(200, payload)

    with patch(
        "src.downloader.time.monotonic",
        side_effect=[100.5, 101.0, 103.0, 103.1, 103.2],
    ), patch("src.downloader.time.sleep") as sleep_mock:
        result = download_source("2310.12345v1", config, client=client)

    assert result.status == "downloaded"
    sleep_mock.assert_called_once()
    assert abs(sleep_mock.call_args.args[0] - 2.0) < 1e-6


def test_download_discovered_sources_preserves_discovery_snapshot(tmp_path):
    _reset_pacing_for_tests()
    config = CrawlConfig(output_dir=tmp_path)
    discovery_path = tmp_path / "state" / "discovery.json"
    discovery_payload = {
        "schema_version": 1,
        "papers": [
            {
                "arxiv_id": "2310.12345",
                "status": "resolved",
                "versions": [
                    {
                        "version": 1,
                        "version_id": "2310.12345v1",
                        "source_url": "https://export.arxiv.org/src/2310.12345v1",
                        "download_status": "pending",
                        "extraction_status": "pending",
                    }
                ],
            },
            {"arxiv_id": "2310.99999", "status": "failed"},
        ],
    }
    write_json(discovery_path, discovery_payload)
    before_bytes = discovery_path.read_bytes()

    payload = _make_tar_gz_bytes([("main.tex", b"\\documentclass{article}")])
    client = Mock()
    client.last_request = None
    client.session = Mock(spec=requests.Session)
    client.session.headers = {}
    client.session.get.return_value = _mock_response(200, payload)

    with patch("src.downloader.time.sleep"):
        report = download_discovered_sources(config, resume=False, client=client)

    assert report["summary"]["expected_versions"] == 1
    assert report["summary"]["downloaded"] == 1
    assert discovery_path.read_bytes() == before_bytes
    assert (tmp_path / "state" / "downloads.json").is_file()
