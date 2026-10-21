# Changelog

本文件记录本项目的显著变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 集成 pre-commit：[`.pre-commit-config.yaml`](.pre-commit-config.yaml) 以 `rev: v0.1.1` 引用
  [init_your_proj](https://github.com/Fishskys/init_your_proj) 的 `iyp-check`（`iyp check --stage --strict`），
  提交前扫描暂存文件拦截敏感信息；误报白名单在项目根 `init_your_proj.toml` 的
  `[check].whitelist_strings`（该文件本地维护、不入库）。

## [0.2.0] - 2026-10-21

统一 `ieee-tools` 命令行入口，并将代码按命令拆分进 `src/ieee_tools/` 包。

### Added

- 统一 CLI 入口 `ieee-tools`，含 `search` / `export_cookies` / `download` / `status` 四个子命令；
  顶层支持 `-h` / `--help` / `help`、`-v` / `--version` / `version`，无参时打印基础命令列表。
- `download` 新增 `--arnumber` / `--doi` / `--url` 直连下载（无需 JSONL 清单）：
  - 均支持逗号分隔多值，例如 `--arnumber 11617481,11526779`；
  - `--url` 从 IEEE Xplore 链接（如 `https://ieeexplore.ieee.org/document/11617481`）自动提取 arnumber；
  - 可与 `--arnumber` / `--doi` 混用并按 arnumber 去重。
- `status` 子命令：本地读取 `download_state.jsonl`，统计各状态并列出失败项，支持 `--json`。
- `export_cookies` 新增 `--output`、`--headless`，并修复 `--help` 会直接拉起浏览器的缺陷。
- 新增 `CHANGELOG.md` 与正式的项目 `AGENTS.md`（此前为未填充模板）。
- 新增 `tests/` pytest 单元测试套件（42 例，不联网、不起浏览器）与 `[dependency-groups] dev`（pytest），
  覆盖查询→目录名、清单加载、直连解析、输出目录解析、`status` 统计、Netscape 写出、CLI 三态与分发。
- 新增 `work/ieee_papers_example.jsonl`（2 条真实记录：一条有 DOI、一条无 DOI）作为论文清单格式样例，
  与 `work/ieee_raw_example.json` 同约定保留入库；`.gitignore` 改为 `work/*` + `!` 白名单，
  修正原先 `work/` 整目录排除导致白名单失效的问题。

### Changed

- 产物目录重构：检索结果默认写入 `work/search_results/<查询>/`（查询空格转下划线、短横保留，如
  `Time-Domain ADC` → `Time-Domain_ADC`），`--dir` 可覆盖；PDF 分类存放——`--jsonl` 下载默认落到该
  JSONL 所在目录，`--arnumber`/`--doi`/`--url` 直连默认落到 `work/papers/`，`--out-dir` 可覆盖。
- 顶层 `papers/` 迁入 `work/papers/`，`.gitignore` 随之调整（`work/` 统一收纳检索结果、PDF 与状态）。
- 源码结构改为按命令拆分：`search.py`（检索）、`cookies.py`（Cookie 导出）、`download.py`（下载 + `status`）、
  `cli.py`（仅做顶层命令分发，无业务逻辑、无子进程）；模块名去掉 `ieee` / `export_ieee` 等前缀。
- `download` 的 PDF 命名、状态键与断点续传统一以 **arnumber** 为准（每篇 IEEE 论文都有 `article_number`）；
  `--doi` 直连先解析为 arnumber 再落盘命名。
- `cookies.py` 改为惰性导入 Playwright：`status` / `search` / `help` 不再被 Playwright 强依赖拖累。
- 检索条件不再写死：`querytext` 默认空，未提供任何检索参数时直接报错退出（退出码 `3`）。
- 检索产物统一按查询主题目录组织，默认 `work/search_results/<查询>/`（`--dir` 可覆盖）。

### Removed

- 移除 `id_type` 配置与 `--id-type` 选项（统一按 arnumber 命名，减少分支与代码复杂度）。
- 移除仓库根目录的三个独立脚本（`ieee_search.py` / `ieee_papers_download.py` / `export_ieee_cookies.py`），
  改为 `src/ieee_tools/` 包内模块，仅通过 `ieee-tools` 调用。
- 移除检索条件、`ID_TYPE` 等写死在脚本顶部的旧默认值。

### Fixed

- 修复 `export_cookies`（原 `export_ieee_cookies.py`）在无参数解析时 `--help` 被忽略并直接启动 Chromium 的问题。
- 统一各子命令的退出码：`0` 成功 / `1` 鉴权失败 / `2` 有失败项或熔断 / `3` 参数或文件错误 / `130` 被 Ctrl+C 中断。

## [0.1.0] - 2025-10-10

首个可运行版本：IEEE Xplore 论文检索、Cookie 导出与批量下载。

### Added

- `ieee_search.py`：调用 IEEE Xplore Metadata API 分页检索论文，自动去重、对 HTTP 403 限流自动重试，
  产出 `work/ieee_papers.jsonl` 与 `work/ieee_raw.json`。
- `export_ieee_cookies.py`：打开 Playwright 自带 Chromium 辅助登录 IEEE Xplore，导出 Netscape 格式 Cookie。
- `ieee_papers_download.py`：按 JSONL 清单批量下载 PDF，支持 `httpx` / `playwright` 双引擎、断点续传、
  限流冷却与熔断、瞬时错误指数退避重试、PDF 完整性校验。

[Unreleased]: https://github.com/Fishskys/ieee_tools/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Fishskys/ieee_tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Fishskys/ieee_tools/releases/tag/v0.1.0
