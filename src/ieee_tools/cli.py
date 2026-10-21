"""ieee-tools — command-line entry point for the IEEE Xplore tools.

Single `ieee-tools` front end:

  ieee-tools                # basic command list
  ieee-tools help           # full help: every command + every parameter
  ieee-tools --version      # version

Subcommands (each implemented in its own module, imported lazily):
  search         检索 IEEE Xplore，保存论文清单到 JSONL          -> search.py
  export_cookies 打开 Chromium 登录并导出 Netscape Cookie        -> cookies.py
  download       按清单或 --arnumber/--doi/--url 批量下载 PDF    -> download.py
  status         查看某目录的下载进度与失败清单                  -> download.py

This module only parses the top-level command and dispatches in-process; it
holds no IEEE logic itself.
"""

from __future__ import annotations

import importlib
import sys

try:  # py3.8+
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version
except ImportError:  # pragma: no cover
    _pkg_version = None  # type: ignore[assignment]
    PackageNotFoundError = Exception  # type: ignore[assignment,misc]

from . import __version__

# command -> (module path, one-line description)
COMMANDS: dict[str, tuple[str, str]] = {
    "search": (
        "ieee_tools.search",
        "检索 IEEE Xplore，保存论文清单到 JSONL",
    ),
    "export_cookies": (
        "ieee_tools.cookies",
        "打开 Chromium 登录并导出 Netscape Cookie（需人工登录）",
    ),
    "download": (
        "ieee_tools.download",
        "按清单或 --arnumber/--doi/--url 批量下载 PDF（支持断点续传）",
    ),
}
STATUS_DESC = "查看某目录的下载进度与失败清单"


def _version() -> str:
    if _pkg_version is not None:
        try:
            return _pkg_version("ieee-tools")
        except PackageNotFoundError:
            pass
    return __version__


def _command_rows() -> list[tuple[str, str]]:
    rows = [(name, desc) for name, (_, desc) in COMMANDS.items()]
    rows.append(("status", STATUS_DESC))
    return rows


def _basic_help() -> str:
    rows = _command_rows()
    width = max(len(name) for name, _ in rows)
    lines = ["ieee-tools <command> [options]", "", "Commands:"]
    for name, desc in rows:
        lines.append(f"  {name.ljust(width)}  {desc}")
    lines += [
        "",
        "-h, --help, help        查看全命令与参数",
        "-v, --version, version  查看版本号",
    ]
    return "\n".join(lines)


def _module_help(module_path: str) -> str:
    """Return a command module's own build_parser() help text."""
    try:
        module = importlib.import_module(module_path)
        return module.build_parser().format_help().strip()
    except Exception as exc:  # pragma: no cover
        return f"(无法获取 {module_path} 的帮助: {exc})"


def _full_help() -> str:
    parts = [
        _basic_help(),
        "",
        "=" * 64,
        "全命令与参数",
        "=" * 64,
    ]
    for name, (module_path, desc) in COMMANDS.items():
        parts += ["", f"## {name} — {desc}", _module_help(module_path)]
    parts += [
        "",
        f"## status — {STATUS_DESC}",
        _status_help(),
        "",
        "## 其他",
        "  ieee-tools -v | --version | version    显示版本号",
        "  ieee-tools -h | --help | help          显示本完整帮助",
        "",
        "退出码: 0 成功 / 1 鉴权失败 / 2 有失败项或熔断 / "
        "3 参数或文件错误 / 130 被 Ctrl+C 中断",
    ]
    return "\n".join(parts)


def _status_help() -> str:
    from .download import build_status_parser

    return build_status_parser().format_help().strip()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No arguments -> basic command list (exit 0).
    if not argv:
        print(_basic_help())
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd in ("-v", "--version", "version"):
        print(f"ieee-tools {_version()}")
        return 0
    if cmd in ("-h", "--help", "help"):
        print(_full_help())
        return 0

    if cmd == "status":
        from .download import status_main

        return status_main(rest)

    if cmd in COMMANDS:
        module = importlib.import_module(COMMANDS[cmd][0])
        return module.main(rest)

    print(f"error: unknown command '{cmd}'\n", file=sys.stderr)
    print(_basic_help(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
