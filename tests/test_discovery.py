import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.config import CrawlConfig
from src.discovery import discover_papers, load_ids, normalize_id, parse_paper


def feed(base="2310.12345", latest=3):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>http://arxiv.org/abs/{base}v{latest}</id>
    <title> A paper\n title </title><author><name>Alice</name></author>
    <published>2023-10-01T00:00:00Z</published>
    <updated>2023-10-03T00:00:00Z</updated></entry></feed>'''.encode()


def test_input_validation(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text("# comment\n2310.12345v2\n2310.12345\nhep-th/9901001\nbad\n", encoding="utf-8")
    result = load_ids(path)
    assert result["ids"] == ["2310.12345", "hep-th/9901001"]
    assert len(result["duplicates"]) == len(result["invalid"]) == 1


def test_versions_and_missing_revision_dates():
    paper = parse_paper(feed(), "2310.12345")
    assert [v["version_id"] for v in paper["versions"]] == [f"2310.12345v{n}" for n in (1, 2, 3)]
    assert paper["title"] == "A paper title"
    assert paper["revised_dates"] == []
    assert not paper["revision_dates_complete"]
    assert parse_paper(feed("hep-th/9901001", 1), "hep-th/9901001")["latest_version"] == 1


def test_mismatched_response():
    with pytest.raises(LookupError):
        parse_paper(feed(), "2310.67890")


def test_partial_failure_and_resume(tmp_path):
    client = Mock()
    client.fetch.side_effect = [feed(), b"<feed xmlns='http://www.w3.org/2005/Atom' />"]
    config = CrawlConfig(output_dir=tmp_path)
    result = discover_papers(["2310.12345", "2310.67890"], config, client=client)
    assert result["summary"]["resolved"] == 1
    assert result["summary"]["failed"] == 1
    client.fetch.reset_mock(side_effect=True)
    client.fetch.return_value = feed("2310.67890", 1)
    result = discover_papers(["2310.12345", "2310.67890"], config, client=client, resume=True)
    client.fetch.assert_called_once_with("2310.67890")
    assert result["summary"]["cache_hits"] == 1
    assert result["summary"]["expected_versions"] == 4
    assert json.loads((tmp_path / "state/discovery.json").read_text())["summary"]["failed"] == 0
