"""Tests for ieee_tools.search — query→目录名 与检索参数校验（不联网）。"""

from __future__ import annotations

import pytest

import ieee_tools.search as search


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Time-Domain ADC", "Time-Domain_ADC"),
        ("ADC", "ADC"),
        ("  Mixed  Signal / ADC  ", "Mixed_Signal_ADC"),
        ("低功耗 ADC", "低功耗_ADC"),
        ("A:B*C?D", "A_B_C_D"),
        ("multi   space", "multi_space"),
        ("dash-kept", "dash-kept"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_slugify_query(query, expected):
    assert search.slugify_query(query) == expected


@pytest.fixture
def search_globals_guard():
    """Snapshot/restore search module globals mutated by main()."""
    names = [
        "querytext",
        "PAPERS_JSONL_FILE",
        "RAW_JSON_FILE",
    ]
    saved = {n: getattr(search, n) for n in names}
    yield
    for n, v in saved.items():
        setattr(search, n, v)


def test_search_default_dir_uses_query_slug(monkeypatch, search_globals_guard):
    """默认产物目录 = work/search_results/<查询slug>/。"""
    monkeypatch.setattr(search, "IEEE_API_KEY", "test-key")
    monkeypatch.setattr(search, "_run_search", lambda: (0, 0, 0, 0))

    rc = search.main(["-q", "Time-Domain ADC"])

    assert rc == search.EXIT_OK
    expected = search.SEARCH_RESULTS_DIR / "Time-Domain_ADC" / "ieee_papers.jsonl"
    assert search.PAPERS_JSONL_FILE == expected
    assert search.RAW_JSON_FILE == expected.parent / "ieee_raw.json"


def test_search_explicit_dir_overrides_default(monkeypatch, search_globals_guard):
    monkeypatch.setattr(search, "IEEE_API_KEY", "test-key")
    monkeypatch.setattr(search, "_run_search", lambda: (0, 0, 0, 0))

    rc = search.main(["-q", "ADC", "--dir", "work/custom/x"])

    assert rc == search.EXIT_OK
    assert search.PAPERS_JSONL_FILE == search.Path("work/custom/x/ieee_papers.jsonl")


def test_search_requires_a_parameter(monkeypatch, capsys):
    monkeypatch.setattr(search, "IEEE_API_KEY", "test-key")
    rc = search.main([])  # no query, no filters
    assert rc == search.EXIT_USAGE
    assert "no search parameter" in capsys.readouterr().err


def test_search_max_records_validation(monkeypatch):
    monkeypatch.setattr(search, "IEEE_API_KEY", "test-key")
    assert search.main(["-q", "x", "--max-records", "999"]) == search.EXIT_USAGE
    assert search.main(["-q", "x", "--max-records", "0"]) == search.EXIT_USAGE


def test_search_missing_api_key(monkeypatch, capsys):
    monkeypatch.setattr(search, "IEEE_API_KEY", "")
    rc = search.main(["-q", "x"])
    assert rc == search.EXIT_USAGE
    assert "API key not found" in capsys.readouterr().err
