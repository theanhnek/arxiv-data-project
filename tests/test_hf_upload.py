"""Offline tests for src/hf_upload.py (no network, huggingface_hub is faked)."""

import sys
import types

import pytest

from src.hf_upload import upload_dataset


class FakeApi:
    calls = []

    def __init__(self, token=None):
        FakeApi.calls.append(("init", token))

    def create_repo(self, **kwargs):
        FakeApi.calls.append(("create_repo", kwargs))

    def upload_folder(self, **kwargs):
        FakeApi.calls.append(("upload_folder", kwargs))


@pytest.fixture
def fake_hub(monkeypatch):
    FakeApi.calls = []
    module = types.ModuleType("huggingface_hub")
    module.HfApi = FakeApi
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    return FakeApi


def test_upload_requires_parquet_files(tmp_path, fake_hub):
    with pytest.raises(FileNotFoundError):
        upload_dataset("user/data", tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        upload_dataset("user/data", tmp_path / "empty")
    assert fake_hub.calls == []


def test_upload_creates_a_public_dataset_and_returns_its_url(tmp_path, fake_hub):
    folder = tmp_path / "parquet" / "papers"
    folder.mkdir(parents=True)
    (folder / "train-00000-of-00001.parquet").write_bytes(b"PAR1")
    url = upload_dataset("user/data", tmp_path / "parquet", token="hf_secret")
    assert url == "https://huggingface.co/datasets/user/data"
    kinds = [c[0] for c in fake_hub.calls]
    assert kinds == ["init", "create_repo", "upload_folder"]
    create = fake_hub.calls[1][1]
    assert create["repo_type"] == "dataset" and create["private"] is False
    assert fake_hub.calls[2][1]["folder_path"] == str(tmp_path / "parquet")
    assert "hf_secret" not in url
