"""Tests for ieee_tools.cli — 顶层命令分发与三态 help/version（不联网、不启浏览器）。"""

from __future__ import annotations

import pytest

import ieee_tools.cli as cli


def test_no_args_prints_command_list(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "ieee-tools <command>" in out
    for cmd in ("search", "export_cookies", "download", "status"):
        assert cmd in out


@pytest.mark.parametrize("arg", ["-h", "--help", "help"])
def test_help_exits_zero(arg, capsys):
    assert cli.main([arg]) == 0
    out = capsys.readouterr().out
    assert "全命令与参数" in out or "ieee-tools <command>" in out


@pytest.mark.parametrize("arg", ["-v", "--version", "version"])
def test_version_exits_zero(arg, capsys):
    assert cli.main([arg]) == 0
    assert capsys.readouterr().out.startswith("ieee-tools ")


def test_unknown_command_exits_two(capsys):
    assert cli.main(["nope"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_status_dispatch(tmp_path, capsys):
    import json

    sp = tmp_path / "download_state.jsonl"
    sp.write_text(json.dumps({"item_key": "1", "status": "success"}) + "\n")
    assert cli.main(["status", "--state", str(sp)]) == 0
    assert "total: 1" in capsys.readouterr().out


def test_full_help_includes_each_command_section(capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    for header in ("## search", "## export_cookies", "## download", "## status"):
        assert header in out
