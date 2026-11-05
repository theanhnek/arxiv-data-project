"""Offline tests for src/references.py: no network, no real sleeping."""

import json
from pathlib import Path

import pytest
import requests

from src.config import CrawlConfig
from src.references import (
    RateLimiter,
    ReferencesRequestError,
    SemanticScholarAuthError,
    SemanticScholarClient,
    collect_references,
    fetch_references,
    normalize_reference,
)
from src.storage import (
    load_references_cache,
    read_json,
    references_cache_path,
    references_report_path,
    write_json_atomic,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeClock:
    """Monotonic clock whose sleep() just moves time forward."""

    def __init__(self):
        self.now_value = 1000.0
        self.sleeps = []

    def now(self):
        return self.now_value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_value += seconds


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, bad_json=False):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Serve queued responses per route; a route is a substring of the request URL."""

    def __init__(self, routes):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        for key, queue in self.routes.items():
            if key in url:
                item = queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
        raise AssertionError(f"Unexpected request: {url}")

    def close(self):
        self.closed = True


def cited(n, arxiv=None, title=None, **extra):
    paper = {
        "paperId": f"s2-{n}",
        "title": title or f"Cited paper {n}",
        "authors": [{"authorId": str(n), "name": f"Author {n}"}],
        "year": 2020,
        "publicationDate": "2020-05-01",
        "venue": "Some Venue",
        "externalIds": {"CorpusId": n},
    }
    if arxiv:
        paper["externalIds"]["ArXiv"] = arxiv
    paper.update(extra)
    return {"citedPaper": paper}


def page(items, next_offset=None, offset=0):
    payload = {"offset": offset, "data": items}
    if next_offset is not None:
        payload["next"] = next_offset
    return payload


def make_client(config, routes, *, window=95):
    clock = FakeClock()
    session = FakeSession(routes)
    limiter = RateLimiter(
        config.s2_delay_seconds,
        config.s2_window_seconds,
        0 if config.s2_api_key else window,
        clock=clock.now,
        sleep=clock.sleep,
    )
    client = SemanticScholarClient(config, session=session, limiter=limiter, sleep=clock.sleep)
    return client, session, clock


def make_config(tmp_path, **overrides):
    settings = {"s2_page_limit": 2, "s2_delay_seconds": 1.0}
    settings.update(overrides)
    return CrawlConfig(output_dir=tmp_path / "data", **settings)


def write_discovery(config, papers):
    write_json_atomic(
        config.output_dir / "state" / "discovery.json",
        {"schema_version": 1, "papers": papers},
    )


def resolved(arxiv_id):
    return {"arxiv_id": arxiv_id, "status": "resolved", "title": f"Paper {arxiv_id}"}


# --------------------------------------------------------------------------- #
# normalize_reference
# --------------------------------------------------------------------------- #
def test_normalize_reference_flat_record():
    ref = normalize_reference(cited(1, arxiv="1706.03762v5", title="  Attention \n is all  "), 7)
    assert ref["index"] == 7
    assert ref["s2_id"] == "s2-1"
    assert ref["title"] == "Attention is all"
    assert ref["authors"] == ["Author 1"]
    assert ref["arxiv_id"] == "1706.03762"
    assert ref["arxiv_id_raw"] == "1706.03762v5"
    assert ref["year"] == 2020
    assert ref["publication_date"] == "2020-05-01"


def test_normalize_reference_never_fabricates_submission_date():
    ref = normalize_reference(cited(1, arxiv="1706.03762"), 0)
    assert ref["publication_date"] == "2020-05-01"
    assert ref["submission_date"] is None


def test_normalize_reference_keeps_old_style_ids_and_flags_invalid():
    old = normalize_reference(cited(1, arxiv="HEP-TH/9901001"), 0)
    assert old["arxiv_id"] == "hep-th/9901001"
    bad = normalize_reference(cited(2, arxiv="not an id"), 1)
    assert bad["arxiv_id"] is None
    assert bad["arxiv_id_raw"] == "not an id"
    none = normalize_reference(cited(3), 2)
    assert none["arxiv_id"] is None and none["arxiv_id_raw"] is None


def test_normalize_reference_tolerates_missing_fields():
    ref = normalize_reference({"citedPaper": {"paperId": None, "title": None}}, 0)
    assert ref["s2_id"] is None and ref["title"] is None
    assert ref["authors"] == [] and ref["year"] is None and ref["arxiv_id"] is None
    assert normalize_reference({"citedPaper": None}, 1) is None
    assert normalize_reference("junk", 2) is None
    bare = normalize_reference({"paperId": "x", "title": "Bare"}, 3)
    assert bare["s2_id"] == "x"


# --------------------------------------------------------------------------- #
# Client: success paths and pagination
# --------------------------------------------------------------------------- #
def test_fetch_single_page(tmp_path):
    config = make_config(tmp_path)
    client, session, _ = make_client(
        config, {"/references": [FakeResponse(payload=page([cited(1, "1706.03762"), cited(2)]))]}
    )
    result = client.fetch_references("2410.13025v2")
    assert result.status == "success"
    assert result.arxiv_id == "2410.13025"
    assert [r["arxiv_id"] for r in result.references] == ["1706.03762", None]
    assert result.request_count == 1 and result.pages == 1
    url, params = session.calls[0]
    assert url.endswith("/paper/arXiv:2410.13025/references")
    assert params == {"fields": params["fields"], "offset": 0, "limit": 2}
    assert "externalIds" in params["fields"]
    record = result.to_record()
    assert record["counts"] == {"total": 2, "with_arxiv_id": 1}
    assert record["granularity"] == "publication_level"
    assert record["raw_pages"][0]["data"][0]["citedPaper"]["paperId"] == "s2-1"


def test_fetch_follows_pagination_until_no_next(tmp_path):
    config = make_config(tmp_path)
    routes = {
        "/references": [
            FakeResponse(payload=page([cited(1), cited(2)], next_offset=2)),
            FakeResponse(payload=page([cited(3), cited(4)], next_offset=4, offset=2)),
            FakeResponse(payload=page([cited(5)], offset=4)),
        ]
    }
    client, session, _ = make_client(config, routes)
    result = client.fetch_references("2410.13025")
    assert result.status == "success"
    assert [r["s2_id"] for r in result.references] == [f"s2-{n}" for n in range(1, 6)]
    assert [params["offset"] for _, params in session.calls] == [0, 2, 4]
    assert result.pages == 3 and result.request_count == 3
    assert [r["index"] for r in result.references] == [0, 1, 2, 3, 4]


def test_pagination_that_does_not_advance_fails(tmp_path):
    config = make_config(tmp_path)
    routes = {"/references": [FakeResponse(payload=page([cited(1)], next_offset=0))]}
    client, _, _ = make_client(config, routes)
    result = client.fetch_references("2410.13025")
    assert result.status == "failed"
    assert result.error_type == "unexpected_response"
    assert result.references == [] and result.raw_pages == []


def test_empty_list_is_distinct_from_not_found(tmp_path):
    config = make_config(tmp_path)
    client, _, _ = make_client(config, {"/references": [FakeResponse(payload=page([]))]})
    assert client.fetch_references("2410.13025").status == "empty_references"
    client2, _, _ = make_client(config, {"/references": [FakeResponse(404, {"error": "x"})]})
    result = client2.fetch_references("2410.13025")
    assert result.status == "not_found" and result.http_status == 404


def test_items_without_a_paper_object_do_not_count(tmp_path):
    config = make_config(tmp_path)
    payload = page([{"citedPaper": None}])
    client, _, _ = make_client(config, {"/references": [FakeResponse(payload=payload)]})
    assert client.fetch_references("2410.13025").status == "empty_references"


# --------------------------------------------------------------------------- #
# Client: errors, retries, throttling
# --------------------------------------------------------------------------- #
def test_429_retry_after_is_honored_then_succeeds(tmp_path):
    config = make_config(tmp_path)
    routes = {
        "/references": [
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(payload=page([cited(1)])),
        ]
    }
    client, _, clock = make_client(config, routes)
    result = client.fetch_references("2410.13025")
    assert result.status == "success" and result.request_count == 2
    assert 7.0 in clock.sleeps


def test_server_errors_exhaust_attempts_and_fail(tmp_path):
    config = make_config(tmp_path, max_attempts=3)
    routes = {"/references": [FakeResponse(500), FakeResponse(502), FakeResponse(503)]}
    client, _, _ = make_client(config, routes)
    result = client.fetch_references("2410.13025")
    assert result.status == "failed"
    assert result.error_type == "http_503" and result.http_status == 503
    assert result.request_count == 3


def test_network_errors_are_retried(tmp_path):
    config = make_config(tmp_path, max_attempts=3)
    routes = {
        "/references": [
            requests.Timeout("slow"),
            requests.ConnectionError("down"),
            FakeResponse(payload=page([cited(1)])),
        ]
    }
    client, _, _ = make_client(config, routes)
    assert client.fetch_references("2410.13025").status == "success"

    routes = {"/references": [requests.Timeout("slow")] * 2}
    client2, _, _ = make_client(make_config(tmp_path, max_attempts=2), routes)
    result = client2.fetch_references("2410.13025")
    assert result.status == "failed" and result.error_type == "Timeout"


def test_auth_errors_raise_instead_of_failing_quietly(tmp_path):
    config = make_config(tmp_path, s2_api_key="bad-key")
    client, _, _ = make_client(config, {"/references": [FakeResponse(403)]})
    with pytest.raises(SemanticScholarAuthError):
        client.fetch_references("2410.13025")


def test_other_client_errors_do_not_retry(tmp_path):
    config = make_config(tmp_path)
    client, session, _ = make_client(config, {"/references": [FakeResponse(400)]})
    result = client.fetch_references("2410.13025")
    assert result.status == "failed" and result.error_type == "http_400"
    assert len(session.calls) == 1


def test_bad_payloads_fail(tmp_path):
    config = make_config(tmp_path)
    client, _, _ = make_client(config, {"/references": [FakeResponse(bad_json=True)]})
    assert client.fetch_references("2410.13025").error_type == "invalid_json"
    client2, _, _ = make_client(config, {"/references": [FakeResponse(payload={"foo": 1})]})
    assert client2.fetch_references("2410.13025").error_type == "unexpected_response"


def test_404_in_the_middle_of_pagination_fails(tmp_path):
    config = make_config(tmp_path)
    routes = {
        "/references": [
            FakeResponse(payload=page([cited(1), cited(2)], next_offset=2)),
            FakeResponse(404),
        ]
    }
    client, _, _ = make_client(config, routes)
    result = client.fetch_references("2410.13025")
    assert result.status == "failed" and result.error_type == "page_not_found"


def test_module_level_fetch_references_uses_given_client(tmp_path):
    config = make_config(tmp_path)
    client, session, _ = make_client(config, {"/references": [FakeResponse(payload=page([]))]})
    result = fetch_references("2410.13025", config, client=client)
    assert result.status == "empty_references"
    assert session.closed is False  # a shared client is not closed by the helper


# --------------------------------------------------------------------------- #
# Rate limiter and config
# --------------------------------------------------------------------------- #
def test_limiter_enforces_minimum_interval():
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock.now, sleep=clock.sleep)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert clock.sleeps == [1.0, 1.0]
    assert clock.now() == pytest.approx(1002.0)


def test_limiter_enforces_sliding_window_without_key():
    clock = FakeClock()
    start = clock.now()
    limiter = RateLimiter(1.0, 10.0, 3, clock=clock.now, sleep=clock.sleep)
    for _ in range(4):
        limiter.wait()
    # Starts at +0, +1, +2; the fourth must wait until the first leaves the 10 s window.
    assert clock.now() - start == pytest.approx(10.0)


def test_limiter_without_window_only_applies_min_interval():
    clock = FakeClock()
    start = clock.now()
    limiter = RateLimiter(1.0, 300.0, 0, clock=clock.now, sleep=clock.sleep)
    for _ in range(200):
        limiter.wait()
    assert clock.now() - start == pytest.approx(199.0)


def test_client_headers_and_window_depend_on_api_key(tmp_path):
    keyed = SemanticScholarClient(make_config(tmp_path, s2_api_key="secret"), session=FakeSession({}))
    assert keyed.session.headers["x-api-key"] == "secret"
    assert keyed._limiter.window_max_requests == 0
    anonymous = SemanticScholarClient(make_config(tmp_path), session=FakeSession({}))
    assert "x-api-key" not in anonymous.session.headers
    assert anonymous._limiter.window_max_requests == 95


def test_client_rejects_faster_than_documented_pacing(tmp_path):
    with pytest.raises(ValueError):
        SemanticScholarClient(make_config(tmp_path, s2_delay_seconds=0.5))
    with pytest.raises(ValueError):
        SemanticScholarClient(make_config(tmp_path, s2_page_limit=5000))


# --------------------------------------------------------------------------- #
# collect_references: cache, report, resume
# --------------------------------------------------------------------------- #
def test_collect_writes_cache_and_report(tmp_path):
    config = make_config(tmp_path)
    write_discovery(
        config,
        [
            resolved("2410.13025"),
            resolved("2410.13026"),
            resolved("2410.13027"),
            resolved("2410.13028"),
            {"arxiv_id": "2410.13029", "status": "failed"},
        ],
    )
    routes = {
        "arXiv:2410.13025/": [FakeResponse(payload=page([cited(1, "1706.03762"), cited(2)]))],
        "arXiv:2410.13026/": [FakeResponse(payload=page([]))],
        "arXiv:2410.13027/": [FakeResponse(404)],
        "arXiv:2410.13028/": [FakeResponse(500)] * 3,
    }
    client, session, _ = make_client(config, routes)
    report = collect_references(config, client=client, progress=None)

    summary = report["summary"]
    assert summary["assigned_papers"] == 4  # the failed discovery paper is not assigned
    assert summary["queried_papers"] == 4
    assert summary["papers_with_references"] == 1
    assert summary["empty_references_papers"] == 1
    assert summary["not_found_papers"] == 1
    assert summary["failed_queries"] == 1
    assert summary["successful_api_queries"] == 2
    assert summary["reference_scrape_success_rate"] == 0.5
    assert summary["total_references_crawled"] == 2
    assert summary["total_arxiv_references_crawled"] == 1
    assert summary["avg_references_per_queried_paper"] == 0.5
    assert summary["avg_references_per_successful_paper"] == 1.0
    assert summary["field_coverage_rates"]["arxiv_id"] == 0.5
    assert summary["field_coverage_rates"]["title"] == 1.0
    assert summary["field_coverage_rates"]["submission_date"] == 0.0
    assert summary["requests_this_run"] == 6
    assert summary["aborted"] is None
    assert report["complete"] is True and report["authenticated"] is False

    on_disk = read_json(references_report_path(config))
    assert on_disk["summary"] == summary
    statuses = {e["arxiv_id"]: e["status"] for e in on_disk["papers"]}
    assert statuses == {
        "2410.13025": "success",
        "2410.13026": "empty_references",
        "2410.13027": "not_found",
        "2410.13028": "failed",
    }
    ok_entry = on_disk["papers"][0]
    assert ok_entry["safe_id"] == "2410-13025"
    assert ok_entry["references_path"] == "corpus/2410-13025/references.json"
    assert on_disk["papers"][3]["references_path"] is None

    assert references_cache_path(config, "2410.13025").is_file()
    assert references_cache_path(config, "2410.13026").is_file()
    assert references_cache_path(config, "2410.13027").is_file()
    assert not references_cache_path(config, "2410.13028").exists()  # failed: not cached
    assert not any(config.output_dir.rglob("*.tmp"))


def test_api_key_is_never_written_to_disk(tmp_path):
    config = make_config(tmp_path, s2_api_key="super-secret-key")
    write_discovery(config, [resolved("2410.13025")])
    client, _, _ = make_client(config, {"/references": [FakeResponse(payload=page([cited(1)]))]})
    report = collect_references(config, client=client, progress=None)
    assert report["authenticated"] is True
    for path in config.output_dir.rglob("*.json"):
        assert "super-secret-key" not in path.read_text(encoding="utf-8")


def test_resume_reuses_good_cache_and_retries_failed(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025"), resolved("2410.13026")])
    first, _, _ = make_client(
        config,
        {
            "arXiv:2410.13025/": [FakeResponse(payload=page([cited(1)]))],
            "arXiv:2410.13026/": [FakeResponse(503)] * 3,
        },
    )
    collect_references(config, client=first, progress=None)

    second, session, _ = make_client(
        config, {"arXiv:2410.13026/": [FakeResponse(payload=page([cited(9, "2101.00001")]))]}
    )
    report = collect_references(config, client=second, resume=True, progress=None)
    assert [url for url, _ in session.calls if "2410.13025" in url] == []
    entries = {e["arxiv_id"]: e for e in report["papers"]}
    assert entries["2410.13025"]["cache_hit"] is True and entries["2410.13025"]["requests"] == 0
    assert entries["2410.13026"]["status"] == "success" and entries["2410.13026"]["cache_hit"] is False
    assert report["summary"]["cache_hits"] == 1
    assert report["summary"]["total_references_crawled"] == 2


def test_without_resume_everything_is_queried_again(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025")])
    for refs in (1, 2):
        client, session, _ = make_client(
            config,
            {"/references": [FakeResponse(payload=page([cited(n) for n in range(refs)]))]},
        )
        report = collect_references(config, client=client, progress=None)
        assert len(session.calls) == 1
        assert report["summary"]["total_references_crawled"] == refs


def test_failed_refresh_does_not_destroy_a_good_cache(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025")])
    good, _, _ = make_client(config, {"/references": [FakeResponse(payload=page([cited(1)]))]})
    collect_references(config, client=good, progress=None)

    bad, _, _ = make_client(config, {"/references": [FakeResponse(500)] * 3})
    report = collect_references(config, client=bad, progress=None)  # no resume: re-query
    assert report["papers"][0]["status"] == "failed"
    cached = load_references_cache(config, "2410.13025")
    assert cached is not None and cached["status"] == "success" and cached["counts"]["total"] == 1


def test_not_found_is_reused_unless_retry_requested(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025")])
    first, _, _ = make_client(config, {"/references": [FakeResponse(404)]})
    collect_references(config, client=first, progress=None)

    reuse, session, _ = make_client(config, {})
    report = collect_references(config, client=reuse, resume=True, progress=None)
    assert session.calls == [] and report["papers"][0]["cache_hit"] is True

    retry, session2, _ = make_client(
        config, {"/references": [FakeResponse(payload=page([cited(1)]))]}
    )
    report2 = collect_references(
        config, client=retry, resume=True, retry_not_found=True, progress=None
    )
    assert len(session2.calls) == 1 and report2["papers"][0]["status"] == "success"


def test_corrupt_or_foreign_cache_is_ignored(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025")])
    path = references_cache_path(config, "2410.13025")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_references_cache(config, "2410.13025") is None

    write_json_atomic(path, {"schema_version": 1, "arxiv_id": "2410.99999", "status": "success", "references": []})
    assert load_references_cache(config, "2410.13025") is None

    client, session, _ = make_client(config, {"/references": [FakeResponse(payload=page([cited(1)]))]})
    report = collect_references(config, client=client, resume=True, progress=None)
    assert len(session.calls) == 1 and report["papers"][0]["status"] == "success"


def test_interrupt_keeps_finished_papers_and_writes_report(tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025"), resolved("2410.13026")])
    client, _, _ = make_client(
        config,
        {
            "arXiv:2410.13025/": [FakeResponse(payload=page([cited(1)]))],
            "arXiv:2410.13026/": [KeyboardInterrupt()],
        },
    )
    with pytest.raises(KeyboardInterrupt):
        collect_references(config, client=client, progress=None)
    report = read_json(references_report_path(config))
    assert report["complete"] is False
    assert [e["arxiv_id"] for e in report["papers"]] == ["2410.13025"]
    assert report["summary"]["not_crawled_papers"] == 1
    assert load_references_cache(config, "2410.13025")["status"] == "success"

    again, session, _ = make_client(
        config, {"arXiv:2410.13026/": [FakeResponse(payload=page([cited(2)]))]}
    )
    final = collect_references(config, client=again, resume=True, progress=None)
    assert final["complete"] is True and final["summary"]["queried_papers"] == 2
    assert all("2410.13025" not in url for url, _ in session.calls)


def test_auth_error_aborts_the_run_and_is_reported(tmp_path):
    config = make_config(tmp_path, s2_api_key="bad")
    write_discovery(config, [resolved("2410.13025"), resolved("2410.13026")])
    client, session, _ = make_client(config, {"/references": [FakeResponse(403)]})
    report = collect_references(config, client=client, progress=None)
    assert report["complete"] is False
    assert "HTTP 403" in report["summary"]["aborted"]
    assert report["summary"]["queried_papers"] == 0
    assert len(session.calls) == 1


def test_explicit_ids_do_not_need_discovery(tmp_path):
    config = make_config(tmp_path)
    client, session, _ = make_client(
        config, {"arXiv:1706.03762/": [FakeResponse(payload=page([cited(1, "1409.0473")]))]}
    )
    report = collect_references(
        config, ids=["1706.03762v7", "1706.03762"], client=client, progress=None
    )
    assert len(session.calls) == 1  # duplicates and version suffixes collapse
    assert report["source"] == "explicit ids"
    assert report["summary"]["total_arxiv_references_crawled"] == 1


def test_missing_discovery_file_has_a_clear_error(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(FileNotFoundError, match="discovery"):
        collect_references(config, client=make_client(config, {})[0], progress=None)


def test_request_error_class_carries_details():
    error = ReferencesRequestError("http_503", "boom", 503)
    assert (error.error_type, error.http_status, str(error)) == ("http_503", 503, "boom")


# --------------------------------------------------------------------------- #
# Config, metrics integration and the --references command
# --------------------------------------------------------------------------- #
def test_config_repr_hides_the_api_key():
    config = CrawlConfig(output_dir=Path("data"), s2_api_key="SECRET-KEY-123")
    assert "SECRET-KEY-123" not in repr(config)
    assert config.s2_api_key == "SECRET-KEY-123"


def test_state_report_is_understood_by_the_metrics_module(tmp_path):
    """src/metrics.py (member 5) must read state/references.json without any adapter."""
    pytest.importorskip("psutil")
    from src.metrics import compute_pipeline_metrics

    config = make_config(tmp_path)
    ids = ["2410.13025", "2410.13026", "2410.13027", "2410.13028"]
    write_discovery(config, [resolved(i) for i in ids])
    routes = {
        "2410.13025": [FakeResponse(payload=page([cited(1, "1706.03762"), cited(2)]))],
        "2410.13026": [FakeResponse(payload=page([]))],
        "2410.13027": [FakeResponse(404, {"error": "not found"})],
        "2410.13028": [FakeResponse(500)] * 3,
    }
    client, _, _ = make_client(config, routes)
    report = collect_references(config, client=client, progress=None)
    assert report["complete"] is True

    summary = compute_pipeline_metrics(config)["references"]
    assert summary["available"] is True
    assert summary["queried_papers"] == 4
    assert summary["papers_with_references"] == 1
    assert summary["empty_references_papers"] == 1
    assert summary["not_found_papers"] == 1
    assert summary["failed_queries"] == 1
    assert summary["successful_api_queries"] == 2
    assert summary["total_references_crawled"] == 2
    assert summary["total_arxiv_references_crawled"] == 1

    per_paper = read_json(config.output_dir / "metrics" / "per_paper_metrics.json")["papers"]
    by_id = {p["arxiv_id"]: p for p in per_paper}
    assert by_id["2410.13025"]["references_status"] == "success"
    assert by_id["2410.13025"]["total_references_count"] == 2
    assert by_id["2410.13025"]["arxiv_references_count"] == 1
    assert by_id["2410.13026"]["references_status"] == "empty_references"
    assert by_id["2410.13027"]["references_status"] == "not_found"
    assert by_id["2410.13028"]["references_status"] == "failed"


def _run_main(monkeypatch, *argv):
    import sys

    import src.main as main_module

    monkeypatch.setattr(sys, "argv", ["src.main", *argv])
    main_module.main()


def test_main_references_flag_uses_one_request_per_second_with_a_key(monkeypatch, tmp_path):
    import src.main as main_module

    seen = {}

    def fake_collect(config, **kwargs):
        seen["config"], seen["kwargs"] = config, kwargs
        return {"summary": {"failed_queries": 0, "aborted": None}}

    monkeypatch.setattr(main_module, "collect_references", fake_collect)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "key-123")
    _run_main(monkeypatch, "--references", "--resume", "--output", str(tmp_path / "data"))
    assert seen["kwargs"] == {"resume": True, "retry_not_found": False}
    assert seen["config"].s2_api_key == "key-123"
    assert seen["config"].s2_delay_seconds == 1.0


def test_main_references_flag_is_conservative_without_a_key(monkeypatch, tmp_path):
    import src.main as main_module

    seen = {}

    def fake_collect(config, **kwargs):
        seen["config"] = config
        return {"summary": {"failed_queries": 0, "aborted": None}}

    monkeypatch.setattr(main_module, "collect_references", fake_collect)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    _run_main(monkeypatch, "--references", "--output", str(tmp_path / "data"))
    assert seen["config"].s2_api_key is None
    assert seen["config"].s2_delay_seconds >= 3.0


def test_main_references_flag_exits_nonzero_on_failures(monkeypatch, tmp_path):
    import src.main as main_module

    monkeypatch.setattr(
        main_module,
        "collect_references",
        lambda config, **kwargs: {"summary": {"failed_queries": 2, "aborted": None}},
    )
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, "--references", "--output", str(tmp_path / "data"))
    assert excinfo.value.code == 1


def test_main_references_without_discovery_reports_a_usage_error(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, "--references", "--output", str(tmp_path / "data"))
    assert excinfo.value.code == 2


def test_main_export_flag_writes_the_two_tables(monkeypatch, tmp_path):
    config = make_config(tmp_path)
    write_discovery(config, [resolved("2410.13025")])
    _run_main(monkeypatch, "--export", "--output", str(config.output_dir))
    papers = (config.output_dir / "export" / "papers.jsonl").read_text(encoding="utf-8")
    assert json.loads(papers.strip())["arxiv_id"] == "2410.13025"
    assert (config.output_dir / "export" / "references.jsonl").read_text(encoding="utf-8") == ""
