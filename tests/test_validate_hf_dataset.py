"""Offline unit tests for scripts/validate_hf_dataset.py (Group 14 pre-upload checks)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.validate_hf_dataset import (
    GROUP14_MEMBERS,
    build_allowed_ids,
    main as validate_main,
    validate_dataset,
)
from src.config import CrawlConfig
from src.storage import export_parquet, save_references_cache

SAMPLE_RANGE = ("2025-10", 1, "2025-10", 100)


class _FakeLoadedSplit:
    def __init__(self, rows, column_names):
        self._rows = rows
        self.column_names = list(column_names)

    def __len__(self):
        return len(self._rows)


@pytest.fixture
def fake_datasets_lib(monkeypatch):
    """Fake Hugging Face ``datasets`` module reading Parquet shards via PyArrow."""
    calls = []

    def fake_load_dataset(path, name=None, split="train"):
        calls.append((path, name, split))
        table_dir = Path(path) / "data" / str(name)
        shards = sorted(table_dir.glob("*.parquet"))
        if not shards:
            raise FileNotFoundError(f"No shards in {table_dir}")
        tables = [pq.read_table(s) for s in shards]
        combined = pa.concat_tables(tables) if len(tables) > 1 else tables[0]
        return _FakeLoadedSplit(combined.to_pylist(), combined.column_names)

    module = types.ModuleType("datasets")
    module.load_dataset = fake_load_dataset
    monkeypatch.setitem(sys.modules, "datasets", module)
    return calls


def _create_valid_sample_export(
    tmp_path: Path, *, paper_ids: tuple[str, str] = ("2510.00001", "2510.00002")
) -> Path:
    """Build a valid exported Parquet dataset using src.storage.export_parquet."""
    config = CrawlConfig(output_dir=tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    id_a, id_b = paper_ids
    discovery = {
        "schema_version": 1,
        "papers": [
            {
                "arxiv_id": id_a,
                "status": "resolved",
                "title": "Sample Paper One",
                "authors": ["Alice Author", "Bob Researcher"],
                "submission_date": "2025-10-01T10:00:00Z",
                "revised_dates": [],
                "venue": "NeurIPS 2025",
                "versions": [{"version": 1, "version_id": f"{id_a}v1"}],
            },
            {
                "arxiv_id": id_b,
                "status": "resolved",
                "title": "Sample Paper Two",
                "authors": ["Carol Scientist"],
                "submission_date": "2025-10-02T08:00:00Z",
                "revised_dates": ["2025-10-05T09:00:00Z"],
                "venue": None,
                "versions": [{"version": 1, "version_id": f"{id_b}v1"}],
            },
        ],
    }
    (state_dir / "discovery.json").write_text(json.dumps(discovery), encoding="utf-8")

    save_references_cache(
        config,
        {
            "schema_version": 1,
            "arxiv_id": id_a,
            "status": "success",
            "references": [
                {
                    "arxiv_id": "1706.03762",
                    "title": "Attention Is All You Need",
                    "authors": ["Ashish Vaswani"],
                    "submission_date": "2017-06-12",
                    "s2_id": "s2-1706",
                },
                {
                    "arxiv_id": None,
                    "title": "Non-arXiv book",
                    "authors": ["Some Author"],
                    "submission_date": None,
                    "s2_id": "s2-book",
                },
            ],
        },
    )
    save_references_cache(
        config,
        {
            "schema_version": 1,
            "arxiv_id": id_b,
            "status": "success",
            "references": [
                {
                    "arxiv_id": "hep-th/9901001",
                    "title": "Legacy arXiv Reference",
                    "authors": ["Legacy Author"],
                    "submission_date": None,
                    "s2_id": "s2-legacy",
                }
            ],
        },
    )

    exported = export_parquet(config)
    return Path(exported["folder"])


def test_official_group14_member_ranges_each_have_5000_ids_and_are_disjoint():
    seen = set()
    for slug in GROUP14_MEMBERS:
        ids, desc = build_allowed_ids(member=slug)
        assert ids is not None and desc is not None
        assert len(ids) == 5000
        assert seen.isdisjoint(ids), f"Range overlap detected for {slug}"
        seen.update(ids)
    assert len(seen) == 25_000


@pytest.mark.parametrize(
    "member,index_str,first_id,last_id,outside_before,outside_after",
    [
        ("Kiet", "1", "2510.14401", "2510.19400", "2510.14400", "2510.19401"),
        ("Duong", "2", "2510.09401", "2510.14400", "2510.09400", "2510.14401"),
        ("Ha", "3", "2510.04401", "2510.09400", "2510.04400", "2510.09401"),
        ("Kien", "4", "2509.26047", "2510.04400", "2509.26046", "2510.04401"),
        ("Anh", "5", "2509.11047", "2509.16046", "2509.11046", "2509.16047"),
    ],
)
def test_every_group14_member_boundary_and_numeric_alias(
    member, index_str, first_id, last_id, outside_before, outside_after
):
    by_slug, _ = build_allowed_ids(member=member)
    by_index, _ = build_allowed_ids(member=index_str)
    assert by_slug is not None and by_slug == by_index
    assert first_id in by_slug
    assert last_id in by_slug
    assert outside_before not in by_slug
    assert outside_after not in by_slug


def test_validate_valid_milestone1_parquet_export(tmp_path, fake_datasets_lib):
    parquet_root = _create_valid_sample_export(tmp_path)
    report = validate_dataset(parquet_root, assigned_range=SAMPLE_RANGE)

    assert report["passed"] is True
    assert report["errors"] == []
    assert report["tables"]["papers"]["rows_count"] == 2
    assert report["tables"]["references"]["rows_count"] == 2
    assert report["tables"]["papers"]["out_of_range_ids_count"] == 0
    assert report["tables"]["papers"]["duplicate_keys_count"] == 0
    assert report["load_dataset"]["checked"] is True
    assert report["load_dataset"]["tables"]["papers"]["ok"] is True
    assert report["load_dataset"]["tables"]["references"]["ok"] is True
    assert len(fake_datasets_lib) == 2


def test_validate_detects_out_of_range_ids_between_members(tmp_path, fake_datasets_lib):
    # Dataset contains IDs from member 1 (2510.14401, 2510.14402)
    parquet_root = _create_valid_sample_export(
        tmp_path, paper_ids=("2510.14401", "2510.14402")
    )
    assert validate_dataset(parquet_root, member="1")["passed"] is True

    # Validating the same files against member 2's range must fail
    wrong_report = validate_dataset(parquet_root, member="2")
    assert wrong_report["passed"] is False
    assert wrong_report["tables"]["papers"]["out_of_range_ids_count"] == 2
    assert any("OUTSIDE assigned range" in err for err in wrong_report["errors"])


def test_validate_detects_duplicates_null_non_null_and_versioned_id(tmp_path, fake_datasets_lib):
    parquet_root = _create_valid_sample_export(tmp_path)
    papers_shard = next((parquet_root / "data" / "papers").glob("*.parquet"))

    schema = pa.schema(
        [
            pa.field("arxiv_id", pa.string(), nullable=True),
            pa.field("title", pa.string()),
            pa.field("authors", pa.list_(pa.string())),
            pa.field("submission_date", pa.string()),
            pa.field("revised_dates", pa.list_(pa.string())),
            pa.field("venue", pa.string()),
        ]
    )
    bad_rows = [
        {
            "arxiv_id": "2510.00001",
            "title": "A",
            "authors": ["X"],
            "submission_date": "2025-10-01",
            "revised_dates": [],
            "venue": None,
        },
        {
            "arxiv_id": "2510.00001",  # duplicate!
            "title": "A dup",
            "authors": ["X"],
            "submission_date": "2025-10-01",
            "revised_dates": [],
            "venue": None,
        },
        {
            "arxiv_id": None,  # null non-null column!
            "title": "Missing ID",
            "authors": [],
            "submission_date": None,
            "revised_dates": [],
            "venue": None,
        },
        {
            "arxiv_id": "2510.00002v2",  # version suffix forbidden!
            "title": "Versioned ID",
            "authors": ["Y"],
            "submission_date": "2025-10-02",
            "revised_dates": [],
            "venue": None,
        },
    ]
    pq.write_table(pa.Table.from_pylist(bad_rows, schema=schema), papers_shard)

    report = validate_dataset(parquet_root, assigned_range=SAMPLE_RANGE)
    assert report["passed"] is False
    assert report["tables"]["papers"]["duplicate_keys_count"] == 1
    assert any("duplicate rows" in e for e in report["errors"])
    assert any("Non-null column 'arxiv_id'" in e for e in report["errors"])
    assert any("version-suffixed" in e for e in report["errors"])


def test_validate_detects_missing_readme_and_orphan_references(tmp_path, fake_datasets_lib):
    parquet_root = _create_valid_sample_export(tmp_path)
    (parquet_root / "README.md").unlink()

    # Add a reference citing a paper not in `papers`
    refs_shard = next((parquet_root / "data" / "references").glob("*.parquet"))
    tbl = pq.read_table(refs_shard)
    extra_row = pa.Table.from_pylist(
        [
            {
                "arxiv_id": "2510.00099",  # in sample range, but not in `papers`!
                "ref_arxiv_id": "1706.03762",
                "ref_title": "Ref",
                "ref_authors": ["A"],
                "ref_submission_date": "2017-06-12",
                "ref_s2_id": "s2-x",
            }
        ],
        schema=tbl.schema,
    )
    pq.write_table(pa.concat_tables([tbl, extra_row]), refs_shard)

    report = validate_dataset(parquet_root, assigned_range=SAMPLE_RANGE)
    assert report["passed"] is False
    assert any("Missing dataset card README.md" in e for e in report["errors"])
    assert any("not present in `papers`" in e for e in report["errors"])


def test_validate_rejects_nested_struct_and_warns_template_placeholders(
    tmp_path, fake_datasets_lib
):
    parquet_root = _create_valid_sample_export(tmp_path)
    readme = parquet_root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nMaintainer: {{HF_USERNAME}}\n",
        encoding="utf-8",
    )

    papers_shard = next((parquet_root / "data" / "papers").glob("*.parquet"))
    nested_table = pa.Table.from_pylist(
        [
            {
                "arxiv_id": "2510.00001",
                "title": "Nested",
                "authors": [{"name": "Alice"}],
                "submission_date": "2025-10-01",
                "revised_dates": [],
                "venue": None,
            }
        ],
        schema=pa.schema(
            [
                pa.field("arxiv_id", pa.string()),
                pa.field("title", pa.string()),
                pa.field("authors", pa.list_(pa.struct([("name", pa.string())]))),
                pa.field("submission_date", pa.string()),
                pa.field("revised_dates", pa.list_(pa.string())),
                pa.field("venue", pa.string()),
            ]
        ),
    )
    pq.write_table(nested_table, papers_shard)

    report = validate_dataset(parquet_root, assigned_range=SAMPLE_RANGE)
    assert report["passed"] is False
    assert any("forbidden nested type" in e for e in report["errors"])
    assert any("must be list<string>" in e for e in report["errors"])
    assert any("{{HF_USERNAME}}" in w for w in report["warnings"])


def test_validate_supports_milestone2_and_3_tables_and_catches_partition_leakage(
    tmp_path, fake_datasets_lib
):
    parquet_root = _create_valid_sample_export(tmp_path)

    pred_dir = parquet_root / "data" / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)
    pred_schema = pa.schema(
        [
            pa.field("arxiv_id", pa.string()),
            pa.field("partition", pa.string()),
            pa.field("citation_key", pa.string()),
            pa.field("groundtruth_arxiv_id", pa.string()),
            pa.field("cand_1", pa.string()),
            pa.field("cand_2", pa.string()),
            pa.field("cand_3", pa.string()),
            pa.field("cand_4", pa.string()),
            pa.field("cand_5", pa.string()),
        ]
    )
    pred_rows = [
        {
            "arxiv_id": "2510.00001",
            "partition": "train",
            "citation_key": "vaswani2017",
            "groundtruth_arxiv_id": "1706.03762",
            "cand_1": "1706.03762",
            "cand_2": None,
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
        {
            "arxiv_id": "2510.00001",
            "partition": "test",  # Same paper in two partitions -> leakage!
            "citation_key": "devlin2018",
            "groundtruth_arxiv_id": None,
            "cand_1": None,
            "cand_2": None,
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
    ]
    pq.write_table(
        pa.Table.from_pylist(pred_rows, schema=pred_schema),
        pred_dir / "train-00000-of-00001.parquet",
    )

    readme = parquet_root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            'data_files: "data/references/*.parquet"\n---',
            'data_files: "data/references/*.parquet"\n- config_name: predictions\n  data_files: "data/predictions/*.parquet"\n---',
        ),
        encoding="utf-8",
    )

    report = validate_dataset(parquet_root, assigned_range=SAMPLE_RANGE)
    assert "predictions" in report["required_tables"]
    assert report["passed"] is False
    assert any("span multiple partitions" in e for e in report["errors"])


def test_cli_returns_zero_on_pass_and_writes_json_report(tmp_path, fake_datasets_lib):
    parquet_root = _create_valid_sample_export(tmp_path)
    out_json = tmp_path / "metrics" / "validation.json"
    code = validate_main(
        [
            "--dataset-dir",
            str(parquet_root),
            "--range",
            "2025-10",
            "1",
            "2025-10",
            "100",
            "--json-report",
            str(out_json),
        ]
    )
    assert code == 0
    assert out_json.is_file()
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["passed"] is True

    # Checking against an unrelated member range fails with exit code 1
    bad_code = validate_main(["--dataset-dir", str(parquet_root), "--member", "1"])
    assert bad_code == 1
