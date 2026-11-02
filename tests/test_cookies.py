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
