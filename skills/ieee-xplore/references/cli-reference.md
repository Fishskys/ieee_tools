# ieee-tools CLI 参考

入口：在仓库根目录 `cd ~/work/hermes/ieee_tools` 后 `uv run ieee-tools <命令> [选项]`。

## 顶层

| 用法 | 作用 | 退出码 |
|---|---|---|
| `ieee-tools` | 打印基础命令列表 | 0 |
| `ieee-tools help` / `-h` / `--help` | 打印全命令与全部参数 | 0 |
| `ieee-tools version` / `-v` / `--version` | 打印版本号 | 0 |
| `ieee-tools <未知命令>` | 报错 + 基础列表 | 2 |

## 统一退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 鉴权失败（Cookie / VPN） |
| 2 | 有失败项或触发限流熔断（CLI 层：无参/未知命令也用 2） |
| 3 | 参数或文件错误 |
| 130 | 被 Ctrl+C 中断 |

## search

`uv run ieee-tools search [选项]`

| 参数 | 默认 | 说明 |
|---|---|---|
| `-q, --query` | 空 | 全文检索关键词；**与其他过滤参数至少填一个，否则退出 3** |
| `--author` / `--title` / `--doi` / `--year` / `--publication` | 空 | 常用过滤，非空才发送 |
| `--start-date` / `--end-date` | 空 | 日期范围（YYYYMMDD） |
| `--max-records` | 200 | 每页记录数，限制 1–200 |
| `--save-count` | 0 | 最多保存多少篇，0 = 全部 |
| `--interval` | 30 | 翻页间隔秒数（避免 QPS 限流），0 = 关闭 |
| `--retry-403-count` / `--retry-403-wait` | 3 / 60 | 403 限流重试次数与等待秒数 |
| `--dir DIR` | `work/search_results/<查询>/` | 产物目录，写 `ieee_papers.jsonl` + `ieee_raw.json`；查询空格转下划线、短横保留 |

## export_cookies

`uv run ieee-tools export_cookies [选项]`（人工登录步骤）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--output` | config/ieee_cookies.txt | Netscape Cookie 输出路径 |
| `--headless` | 关 | 无头运行（仅已有登录态时有用） |

## download

`uv run ieee-tools download [选项]`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--jsonl` | work/ieee_papers.jsonl | 论文清单（给出 `--arnumber`/`--doi`/`--url` 时忽略）；PDF 默认落到该清单所在目录 |
| `--arnumber N[,N...]` | — | 直接按 arnumber 下载，逗号分隔多个 |
| `--doi DOI[,DOI...]` | — | 直接按 DOI 下载，逗号分隔多个 |
| `--url URL[,URL...]` | — | 直接按 IEEE 链接下载，逗号分隔多个（自动提取 arnumber） |
| `--out-dir` | 清单同目录 / `work/papers/` | PDF 输出目录：`--jsonl` 下载默认落到该 JSONL 所在目录，直连下载默认 `work/papers/` |
| `--cookies` | config/ieee_cookies.txt | Netscape Cookie 文件 |
| `--state` | 输入 JSONL 同目录 `download_state.jsonl` | 下载状态文件 |
| `--engine {playwright,httpx}` | playwright | 下载引擎（playwright 过风控） |
| `--delay` | 20 | 篇间基础延时秒（实际 ±3s），0 = 不等待 |
| `--limit N` | 0 | 本次最多处理 N 篇，0 = 全部 |
| `--retry-failed` | 关 | 只处理状态里此前非成功的项 |
| `--force` | 关 | 已有有效 PDF 也重新下载 |
| `--cooldown-seconds` | 1200 | 遇限流冷却秒数 |
| `--max-rate-limits` | 3 | 连续多少次限流后熔断退出 |

## status

`uv run ieee-tools status [选项]`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dir` | — | 主题目录，读其下 `download_state.jsonl` |
| `--state` | — | 直接指定 state 文件路径（`--dir` 与 `--state` 都不给时，默认 `work/download_state.jsonl`） |
| `--json` | 关 | 机器可读 JSON（Agent 解析用） |

人类可读输出示例：

```
state: work/search_results/Time-Domain_ADC/download_state.jsonl
total: 120  success: 112  failed: 8
failed:
  11659999  not_pdf  Response is not PDF ...
```

`--json` 输出结构：`{path, total, counts: {status: n}, failed: [{item_key, status, error, title}]}`。
