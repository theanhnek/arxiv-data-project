"""Offline tests for src/storage.py."""

import json
from pathlib import Path

import pytest

from src.config import CrawlConfig
from src.storage import (
    build_papers_rows,
    build_references_rows,
    export_tables,
    load_discovery_papers,
    load_references_cache,
    paper_dir,
    paper_dirname,
    read_json,
    references_cache_path,
    save_references_cache,
    write_json_atomic,
    write_jsonl_atomic,
)


def make_config(tmp_path):
    return CrawlConfig(output_dir=tmp_path / "data")


def reference(n, arxiv=None, **extra):
    record = {
        "index": n,
        "s2_id": f"s2-{n}",
        "title": f"Title {n}",
        "authors": [f"Author {n}"],
        "year": 2020,
        "publication_date": "2020-05-01",
        "arxiv_id": arxiv,
        "submission_date": None,
    }
    record.update(extra)
    return record


def cache_record(arxiv_id, references, status="success"):
    return {
        "schema_version": 1,
        "arxiv_id": arxiv_id,
        "status": status,
        "references": references,
        "counts": {"total": len(references), "with_arxiv_id": 0},
    }


def write_discovery(config, papers):
    write_json_atomic(
        config.output_dir / "state" / "discovery.json", {"schema_version": 1, "papers": papers}
    )


def test_write_json_atomic_round_trip_and_unicode(tmp_path):
    path = tmp_path / "a" / "b.json"
    write_json_atomic(path, {"title": "Đồ án — Nhập môn"})
    assert read_json(path) == {"title": "Đồ án — Nhập môn"}
    assert "Đồ án" in path.read_text(encoding="utf-8")  # not escaped to \u sequences


def test_failed_write_keeps_old_file_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"ok": True})
    with pytest.raises(TypeError):
        write_json_atomic(path, {"bad": {1, 2, 3}})  # sets are not JSON serializable
    with pytest.raises(ValueError):
        write_json_atomic(path, {"bad": float("nan")})  # NaN would be invalid JSON
    assert read_json(path) == {"ok": True}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_read_json_returns_default_for_missing_or_corrupt(tmp_path):
    assert read_json(tmp_path / "missing.json", default="d") == "d"
    broken = tmp_path / "broken.json"
    broken.write_text('{"half": ', encoding="utf-8")
    assert read_json(broken) is None
    broken.write_bytes(b"\xff\xfe\x00")
    assert read_json(broken, default=[]) == []


def test_paper_dirname_matches_downloader_mapping(tmp_path):
    assert paper_dirname("2410.13025") == "2410-13025"
    assert paper_dirname("arXiv:2410.13025v3") == "2410-13025"
    assert paper_dirname("HEP-TH/9901001") == "hep-th-9901001"
    config = make_config(tmp_path)
    assert paper_dir(config, "2410.13025") == config.output_dir / "corpus" / "2410-13025"
    assert references_cache_path(config, "2410.13025").name == "references.json"
    for bad in ("../etc/passwd", "2410.13025/../x", ""):
        with pytest.raises(ValueError):
            paper_dirname(bad)


def test_load_discovery_papers_filters_and_reports_missing_file(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(FileNotFoundError, match="discovery"):
        load_discovery_papers(config.output_dir / "state" / "discovery.json")
    write_discovery(
        config,
        [
            {"arxiv_id": "2410.13025", "status": "resolved"},
            {"arxiv_id": "2410.13026", "status": "failed"},
        ],
    )
    papers = load_discovery_papers(config.output_dir / "state" / "discovery.json")
    assert [p["arxiv_id"] for p in papers] == ["2410.13025"]


def test_cache_round_trip_and_failed_results_are_refused(tmp_path):
    config = make_config(tmp_path)
    record = cache_record("2410.13025", [reference(1, "1706.03762")])
    path = save_references_cache(config, record)
    assert path == references_cache_path(config, "2410.13025")
    assert load_references_cache(config, "2410.13025v2")["references"][0]["s2_id"] == "s2-1"
    with pytest.raises(ValueError):
        save_references_cache(config, cache_record("2410.13025", [], status="failed"))
    assert load_references_cache(config, "2410.13025")["status"] == "success"  # untouched


def test_cache_validation_rejects_wrong_schema_paper_status_or_shape(tmp_path):
    config = make_config(tmp_path)
    path = references_cache_path(config, "2410.13025")
    cases = [
        {**cache_record("2410.13025", []), "schema_version": 99},
        cache_record("2410.99999", []),
        cache_record("2410.13025", [], status="failed"),
        {**cache_record("2410.13025", []), "references": "nope"},
        ["not", "a", "dict"],
    ]
    for bad in cases:
        write_json_atomic(path, bad)
        assert load_references_cache(config, "2410.13025") is None


def test_papers_rows_follow_the_milestone_columns(tmp_path):
    config = make_config(tmp_path)
    write_discovery(
        config,
        [
            {
                "arxiv_id": "2410.13025",
                "status": "resolved",
                "title": "A paper",
                "authors": ["Alice", "", "Bob"],
                "submission_date": "2024-10-17T10:00:00Z",
                "revised_dates": [],
                "venue": None,
            },
            {"arxiv_id": "2410.13026", "status": "resolved", "title": "", "authors": None},
            {"arxiv_id": "2410.13027", "status": "failed"},
        ],
    )
    rows = build_papers_rows(config)
    assert [list(r) for r in rows] == [
        ["arxiv_id", "title", "authors", "submission_date", "revised_dates", "venue"]
    ] * 2
    assert rows[0]["authors"] == ["Alice", "Bob"]
    assert rows[0]["revised_dates"] == [] and rows[0]["venue"] is None
    assert rows[1]["title"] is None and rows[1]["authors"] == []


def test_references_rows_keep_only_arxiv_keyed_unique_references(tmp_path):
    config = make_config(tmp_path)
    write_discovery(
        config,
        [
            {"arxiv_id": "2410.13025", "status": "resolved"},
            {"arxiv_id": "2410.13026", "status": "resolved"},
            {"arxiv_id": "2410.13027", "status": "resolved"},
        ],
    )
    save_references_cache(
        config,
        cache_record(
            "2410.13025",
            [
                reference(1, "1706.03762"),
                reference(2, None),  # no arXiv ID: stays local, not exported
                reference(3, "1706.03762"),  # duplicate for the same citing paper
                reference(4, "hep-th/9901001"),
            ],
        ),
    )
    save_references_cache(config, cache_record("2410.13026", [], status="empty_references"))
    # 2410.13027 was never queried
    rows = build_references_rows(config)
    assert [(r["arxiv_id"], r["ref_arxiv_id"]) for r in rows] == [
        ("2410.13025", "1706.03762"),
        ("2410.13025", "hep-th/9901001"),
    ]
    first = rows[0]
    assert list(first) == [
        "arxiv_id",
        "ref_arxiv_id",
        "ref_title",
        "ref_authors",
        "ref_submission_date",
        "ref_s2_id",
    ]
    assert first["ref_authors"] == ["Author 1"] and first["ref_s2_id"] == "s2-1"
    # publication_date/year exist on the reference but must not become a submission date
    assert first["ref_submission_date"] is None


def test_same_reference_in_two_papers_gives_one_row_per_citing_paper(tmp_path):
    config = make_config(tmp_path)
    write_discovery(
        config,
        [
            {"arxiv_id": "2410.13025", "status": "resolved"},
            {"arxiv_id": "2410.13026", "status": "resolved"},
        ],
    )
    for paper in ("2410.13025", "2410.13026"):
        save_references_cache(config, cache_record(paper, [reference(1, "1706.03762")]))
    rows = build_references_rows(config)
    assert [r["arxiv_id"] for r in rows] == ["2410.13025", "2410.13026"]


def test_export_tables_writes_json_lines(tmp_path):
    config = make_config(tmp_path)
    write_discovery(
        config,
        [{"arxiv_id": "2410.13025", "status": "resolved", "title": "T", "authors": ["A"]}],
    )
    save_references_cache(
        config, cache_record("2410.13025", [reference(1, "1706.03762", title="Tiêu đề")])
    )
    result = export_tables(config)
    assert result["papers_rows"] == 1 and result["references_rows"] == 1
    papers = [json.loads(l) for l in Path(result["papers_path"]).read_text(encoding="utf-8").splitlines()]
    refs = [json.loads(l) for l in Path(result["references_path"]).read_text(encoding="utf-8").splitlines()]
    assert papers[0]["arxiv_id"] == "2410.13025"
    assert refs[0]["ref_arxiv_id"] == "1706.03762" and refs[0]["ref_title"] == "Tiêu đề"
    assert not any(config.output_dir.rglob("*.tmp"))


def test_write_jsonl_atomic_counts_rows_and_cleans_up_on_error(tmp_path):
    path = tmp_path / "rows.jsonl"
    assert write_jsonl_atomic(path, ({"n": n} for n in range(3))) == 3
    assert path.read_text(encoding="utf-8").splitlines() == ['{"n": 0}', '{"n": 1}', '{"n": 2}']

    def broken():
        yield {"n": 1}
        raise RuntimeError("generator failed")

    with pytest.raises(RuntimeError):
        write_jsonl_atomic(path, broken())
    assert path.read_text(encoding="utf-8").count("\n") == 3  # old content intact
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.jsonl"]
