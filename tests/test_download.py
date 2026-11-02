"""Tests for ieee_tools.download — 清单加载、直连解析、输出目录解析、status（不联网）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ieee_tools.download as download


# --------------------------------------------------------------------------
# build_direct_items
# --------------------------------------------------------------------------

def test_build_direct_arnumber_comma_and_dedup():
    items = download.build_direct_items(["11617481, 11526779,11617481"], None)
    assert [i.item_key for i in items] == ["11617481", "11526779"]
    assert items[0].arnumber == "11617481"
    assert items[0].doi is None


def test_build_direct_url_extracts_arnumber():
    items = download.build_direct_items(
        None,
        None,
        [
            "https://ieeexplore.ieee.org/document/11617481",
            "https://ieeexplore.ieee.org/abstract/document/11526779?tp=&arnumber=11526779",
        ],
    )
    assert [i.item_key for i in items] == ["11617481", "11526779"]
    assert all(i.arnumber == i.item_key for i in items)


def test_build_direct_doi_is_resolved_later():
    items = download.build_direct_items(None, ["10.1109/JSSC.2026.3693653"])
    assert len(items) == 1
    assert items[0].item_key == "doi:10.1109/jssc.2026.3693653"
    assert items[0].arnumber is None
    assert items[0].doi == "10.1109/JSSC.2026.3693653"


def test_build_direct_dedupes_across_sources():
    items = download.build_direct_items(
        ["11617481"], None, ["https://ieeexplore.ieee.org/document/11617481"]
    )
    assert [i.item_key for i in items] == ["11617481"]


def test_build_direct_rejects_bad_arnumber():
    with pytest.raises(ValueError, match="must be numeric"):
        download.build_direct_items(["123,abc"], None)


def test_build_direct_rejects_url_without_arnumber():
    with pytest.raises(ValueError, match="no arnumber"):
        download.build_direct_items(None, None, ["https://example.com/foo"])


# --------------------------------------------------------------------------
# load_papers_jsonl
# --------------------------------------------------------------------------

def test_load_papers_jsonl_keys_by_arnumber(tmp_path):
    p = tmp_path / "ieee_papers.jsonl"
    lines = [
        {"article_number": "11617481", "title": "A", "doi": "10.1/a"},
        {"arnumber": "11526779", "title": "B"},
        {"title": "no id"},  # skipped (no arnumber)
        {"article_number": "11617481", "title": "dup"},  # skipped (duplicate)
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")

    items = download.load_papers_jsonl(p)

    assert [i.item_key for i in items] == ["11617481", "11526779"]
    assert items[0].doi == "10.1/a"
    assert items[1].doi is None


def test_load_papers_jsonl_rejects_bad_json(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json}\n")
    with pytest.raises(ValueError, match="Invalid JSON"):
        download.load_papers_jsonl(p)


# --------------------------------------------------------------------------
# resolve_out_dir
# --------------------------------------------------------------------------

def test_resolve_out_dir_explicit_wins(tmp_path):
    out = download.resolve_out_dir(Path("/tmp/xx"), True, tmp_path / "a.jsonl")
    assert out == Path("/tmp/xx")


def test_resolve_out_dir_direct_goes_to_work_papers(tmp_path):
    out = download.resolve_out_dir(None, True, tmp_path / "a.jsonl")
    assert out == download.PAPERS_DIR
    assert out == download.WORK_DIR / "papers"


def test_resolve_out_dir_jsonl_goes_to_jsonl_parent(tmp_path):
    jp = tmp_path / "Q" / "ieee_papers.jsonl"
    out = download.resolve_out_dir(None, False, jp)
    assert out == jp.parent


# --------------------------------------------------------------------------
# status_main
# --------------------------------------------------------------------------

def _write_state(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_status_main_text(tmp_path, capsys):
    sp = tmp_path / "download_state.jsonl"
    _write_state(
        sp,
        [
            {"item_key": "1", "status": "success"},
            {"item_key": "2", "status": "success"},
            {"item_key": "3", "status": "not_pdf", "error": "html"},
        ],
    )
    rc = download.status_main(["--state", str(sp)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "total: 3" in out
    assert "success: 2" in out
    assert "not_pdf" in out


def test_status_main_json(tmp_path, capsys):
    sp = tmp_path / "download_state.jsonl"
    _write_state(
        sp,
        [
            {"item_key": "1", "status": "success"},
            {"item_key": "2", "status": "rate_limited"},
        ],
    )
    rc = download.status_main(["--state", str(sp), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["total"] == 2
    assert data["counts"] == {"success": 1, "rate_limited": 1}
    assert data["failed"][0]["item_key"] == "2"


def test_status_main_latest_record_wins(tmp_path, capsys):
    sp = tmp_path / "download_state.jsonl"
    _write_state(
        sp,
        [
            {"item_key": "1", "status": "rate_limited"},
            {"item_key": "1", "status": "success"},
        ],
    )
    rc = download.status_main(["--state", str(sp), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["total"] == 1
    assert data["counts"] == {"success": 1}


def test_status_main_missing_file(tmp_path, capsys):
    rc = download.status_main(["--state", str(tmp_path / "nope.jsonl")])
    assert rc == 3
    assert "state file not found" in capsys.readouterr().err


# --------------------------------------------------------------------------
# run_downloads — a single item's unexpected error must NOT abort the batch
# --------------------------------------------------------------------------

class _FakeDownloader:
    """Minimal downloader stand-in: returns bytes per item_key or raises."""

    engine_name = "fake"

    def __init__(self, behavior):
        self.behavior = behavior

    def download(self, item):
        value = self.behavior[item.item_key]
        if isinstance(value, Exception):
            raise value
        return (item.arnumber or item.item_key), value


def test_run_downloads_survives_unexpected_exception(tmp_path):
    items = [
        download.PaperItem("111", "t1", "111", None),
        download.PaperItem("222", "t2", "222", None),
    ]
    pdf = b"%PDF-1.4\n" + b"x" * 20000 + b"\n%%EOF\n"
    fake = _FakeDownloader({"111": ConnectionResetError("tls boom"), "222": pdf})
    state = tmp_path / "state.jsonl"

    rc = download.run_downloads(
        items,
        fake,
        out_dir=tmp_path,
        state_path=state,
        delay=0,
        limit=0,
        retry_failed=False,
        force=False,
        cooldown_seconds=0,
        max_consecutive_rate_limits=3,
    )

    # The second item is still downloaded despite the first one blowing up.
    assert (tmp_path / "222.pdf").is_file()
    recs = [json.loads(l) for l in state.read_text().splitlines() if l.strip()]
    status_by_key = {r["item_key"]: r["status"] for r in recs}
    assert status_by_key["111"] == "error"
    assert status_by_key["222"] == "success"
    assert rc == download.EXIT_FAILED


def test_run_downloads_doi_mode_skips_when_recorded_arnumber_pdf_exists(tmp_path):
    pdf = b"%PDF-1.4\n" + b"x" * 20000 + b"\n%%EOF\n"
    (tmp_path / "8888888.pdf").write_bytes(pdf)  # already downloaded previously
    state = tmp_path / "state.jsonl"
    _write_state(
        state,
        [{
            "item_key": "doi:10.1109/x.y",
            "status": "success",
            "arnumber": "8888888",
            "doi": "10.1109/x.y",
        }],
    )

    class _Boom:
        engine_name = "boom"

        def download(self, it):
            raise AssertionError("must not re-download when arnumber PDF exists")

    item = download.PaperItem("doi:10.1109/x.y", "", None, "10.1109/x.y")
    rc = download.run_downloads(
        [item],
        _Boom(),
        out_dir=tmp_path,
        state_path=state,
        delay=0,
        limit=0,
        retry_failed=False,
        force=False,
        cooldown_seconds=0,
        max_consecutive_rate_limits=3,
    )
    assert rc == download.EXIT_OK


# --------------------------------------------------------------------------
# resolve_arnumber — Crossref first + retry on transient errors
# --------------------------------------------------------------------------

_CROSSREF_WITH_ARN = json.dumps(
    {"message": {"resource": {"primary": {
        "URL": "https://ieeexplore.ieee.org/document/8888888"}}}}
).encode()


class _ResolveProbe(download.BaseDownloader):
    """Fake downloader whose http_resolve is scripted per channel."""

    def __init__(self, crossref_results, doi_results):
        self.crossref_results = list(crossref_results)
        self.doi_results = list(doi_results)
        self.calls = []

    def http_resolve(self, url):
        self.calls.append(url)
        bucket = self.crossref_results if "crossref" in url else self.doi_results
        r = bucket.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_resolve_prefers_crossref_and_retries(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)
    probe = _ResolveProbe(
        crossref_results=[
            download.RetriableDownloadError("tls boom"),
            (200, "", _CROSSREF_WITH_ARN),
        ],
        doi_results=[],
    )
    arn = probe.resolve_arnumber("10.1109/TCSI.2020.1234567")
    assert arn == "8888888"
    # doi.org was never needed because Crossref (after a retry) succeeded.
    assert all("doi.org" not in u for u in probe.calls)


def test_resolve_falls_back_to_doi_org(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)
    probe = _ResolveProbe(
        crossref_results=[(200, "", b'{"message": {}}')],  # no arnumber
        doi_results=[
            (302, "https://ieeexplore.ieee.org/document/7654321", b""),
        ],
    )
    arn = probe.resolve_arnumber("10.1109/X.Y")
    assert arn == "7654321"


def test_resolve_transient_exhausted_raises_downloaderror(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *_: None)
    probe = _ResolveProbe(
        crossref_results=[download.RetriableDownloadError("down")] * (
            download.HTTP_RETRY_COUNT + 1
        ),
        doi_results=[download.RetriableDownloadError("down")] * (
            download.HTTP_RETRY_COUNT + 1
        ),
    )
    with pytest.raises(download.DownloadError):
        probe.resolve_arnumber("10.1109/X.Y")


