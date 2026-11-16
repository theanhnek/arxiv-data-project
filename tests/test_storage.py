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


# --------------------------------------------------------------------------- #
# Parquet export (needs pyarrow)
# --------------------------------------------------------------------------- #


def _papers_and_refs(config, n_papers=3):
    write_discovery(
        config,
        [
            {
                "arxiv_id": f"2410.{13025 + i}",
                "status": "resolved",
                "title": f"Paper {i}",
                "authors": ["A", "B"] if i == 0 else [],
                "submission_date": "2024-10-17T10:00:00Z",
                "revised_dates": [],
                "venue": None,
            }
            for i in range(n_papers)
        ],
    )
    for i in range(n_papers):
        save_references_cache(
            config,
            cache_record(f"2410.{13025 + i}", [reference(1, "1706.03762", title="Tiêu đề")]),
        )


def test_export_parquet_writes_sharded_tables_that_round_trip(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    from src.storage import export_parquet

    config = make_config(tmp_path)
    _papers_and_refs(config, 3)
    result = export_parquet(config, shard_rows=2)

    assert result["papers_rows"] == 3 and result["references_rows"] == 3
    names = sorted(Path(p).name for p in result["papers_shards"])
    assert names == ["train-00000-of-00002.parquet", "train-00001-of-00002.parquet"]

    rows = []
    for shard in sorted(result["papers_shards"]):
        rows += pq.read_table(shard).to_pylist()
    assert [r["arxiv_id"] for r in rows] == ["2410.13025", "2410.13026", "2410.13027"]
    assert rows[0]["authors"] == ["A", "B"] and rows[1]["authors"] == []
    assert rows[0]["venue"] is None and rows[0]["revised_dates"] == []

    refs = pq.read_table(result["references_shards"][0]).to_pylist()
    assert refs[0]["ref_arxiv_id"] == "1706.03762" and refs[0]["ref_title"] == "Tiêu đề"
    assert refs[0]["ref_authors"] == ["Author 1"] and refs[0]["ref_submission_date"] is None
    assert not any(config.output_dir.rglob("*.tmp"))


def test_export_parquet_empty_tables_keep_their_schema(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    from src.storage import PAPERS_COLUMNS, REFERENCES_COLUMNS, export_parquet

    config = make_config(tmp_path)
    write_discovery(config, [])
    result = export_parquet(config)
    assert result["papers_rows"] == 0 and len(result["papers_shards"]) == 1
    assert tuple(pq.read_table(result["papers_shards"][0]).column_names) == PAPERS_COLUMNS
    assert (
        tuple(pq.read_table(result["references_shards"][0]).column_names) == REFERENCES_COLUMNS
    )


def test_export_parquet_rerun_removes_stale_shards(tmp_path):
    pytest.importorskip("pyarrow")
    from src.storage import export_parquet

    config = make_config(tmp_path)
    _papers_and_refs(config, 3)
    export_parquet(config, shard_rows=1)
    folder = config.output_dir / "export" / "parquet" / "data" / "papers"
    assert len(list(folder.glob("*.parquet"))) == 3
    export_parquet(config, shard_rows=10)
    assert [p.name for p in folder.glob("*.parquet")] == ["train-00000-of-00001.parquet"]


def test_failed_parquet_write_keeps_previous_shards(tmp_path, monkeypatch):
    pq = pytest.importorskip("pyarrow.parquet")
    from src.storage import export_parquet

    config = make_config(tmp_path)
    _papers_and_refs(config, 2)
    export_parquet(config)
    folder = config.output_dir / "export" / "parquet" / "data" / "papers"
    before = sorted(p.name for p in folder.glob("*"))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", boom)
    with pytest.raises(OSError):
        export_parquet(config, shard_rows=1)
    assert sorted(p.name for p in folder.glob("*")) == before


def test_export_parquet_writes_dataset_card_once_and_rejects_bad_shard_size(tmp_path):
    pytest.importorskip("pyarrow")
    from src.storage import export_parquet, write_parquet_shards

    config = make_config(tmp_path)
    write_discovery(config, [])
    export_parquet(config)
    card = config.output_dir / "export" / "parquet" / "README.md"
    assert "config_name: papers" in card.read_text(encoding="utf-8")
    card.write_text("edited by hand", encoding="utf-8")
    export_parquet(config)
    assert card.read_text(encoding="utf-8") == "edited by hand"
    with pytest.raises(ValueError):
        write_parquet_shards([], ("arxiv_id",), tmp_path / "x", shard_rows=0)


def test_main_export_parquet_flag_writes_shards(monkeypatch, tmp_path):
    pytest.importorskip("pyarrow")
    import sys

    import src.main as main_module

    config = make_config(tmp_path)
    write_discovery(config, [])
    monkeypatch.setattr(
        sys, "argv", ["src.main", "--export-parquet", "--output", str(config.output_dir)]
    )
    main_module.main()
    assert list((config.output_dir / "export" / "parquet" / "data" / "papers").glob("*.parquet"))


def test_parquet_layout_and_non_null_columns_follow_the_specification(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    from src.storage import export_parquet

    config = make_config(tmp_path)
    _papers_and_refs(config, 1)
    result = export_parquet(config)
    root = config.output_dir / "export" / "parquet"
    assert (root / "README.md").is_file()
    assert (root / "data" / "papers").is_dir() and (root / "data" / "references").is_dir()
    assert 'data_files: "data/references/*.parquet"' in (root / "README.md").read_text(
        encoding="utf-8"
    )
    papers_schema = pq.read_schema(result["papers_shards"][0])
    refs_schema = pq.read_schema(result["references_shards"][0])
    assert papers_schema.field("arxiv_id").nullable is False
    assert papers_schema.field("title").nullable is True
    assert refs_schema.field("arxiv_id").nullable is False
    assert refs_schema.field("ref_arxiv_id").nullable is False
    assert str(papers_schema.field("authors").type) == "list<element: string>"
