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
