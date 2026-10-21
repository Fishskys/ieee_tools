"""Tests for ieee_tools.cookies — Netscape 写出与惰性 Playwright（不启浏览器）。"""

from __future__ import annotations

import ieee_tools.cookies as cookies


def test_write_netscape_cookie_file(tmp_path):
    out = tmp_path / "cookies.txt"
    sample = [
        {"domain": ".ieee.org", "path": "/", "expires": 123,
         "httpOnly": True, "secure": True, "name": "a", "value": "1"},
        {"domain": "ieeexplore.ieee.org", "path": "/x", "expires": -1,
         "httpOnly": False, "secure": False, "name": "b", "value": "2"},
    ]
    cookies.write_netscape_cookie_file(sample, out)

    text = out.read_text()
    assert text.startswith("# Netscape HTTP Cookie File")
    # httpOnly domain gets the #HttpOnly_ prefix
    assert "#HttpOnly_.ieee.org" in text
    # subdomain TRUE only when domain starts with '.'
    assert "\tTRUE\t" in text  # the .ieee.org row
    assert "\tFALSE\t" in text  # the non-dot row
    # expires -1 normalises to 0
    assert "\t0\tb\t2" in text


def test_output_default_is_under_repo_config():
    # OUTPUT is anchored to repo config dir, not cwd.
    assert cookies.OUTPUT.name == "ieee_cookies.txt"
    assert cookies.OUTPUT.parent.name == "config"


def test_importing_cookies_does_not_require_playwright():
    # Module import must not fail even if Playwright is absent, because
    # cli.status / cli.help import chain must stay lightweight.
    assert callable(cookies.main)
    assert callable(cookies.build_parser)
    assert callable(cookies._import_playwright)
