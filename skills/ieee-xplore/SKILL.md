---
name: ieee-xplore
description: "检索、导出登录态并批量下载 IEEE Xplore 论文时用。基于 ieee-tools CLI。"
version: 0.2.0
metadata:
  hermes:
    tags: [Research, IEEE, Papers, Download]
    related_skills: [pdf]
---

# IEEE Xplore 论文检索与下载

面向 Agent 的编排层：**检索 → 导出 Cookie → 批量下载 → 查看进度**。所有动作都通过仓库里的
`ieee-tools` 命令完成，**不要改源码、不要写 `.env`**。

仓库位置：`~/work/hermes/ieee_tools`（下称 `$IEEE_HOME`）。命令都在该目录下用 `uv run` 执行。

## 前置检查

1. **API Key**：确认 `$IEEE_HOME/.env` 有 `IEEE_API_KEY=...`。缺失 → 提示主人到
   <https://developer.ieee.org/> 申请，**不要猜、不要编、不要代填**。
2. **Cookie**：`$IEEE_HOME/config/ieee_cookies.txt` 存在且有效；失效则跑 `export_cookies`
   （人工登录步骤，见工作流第 3 步）。
3. **Chromium**：`uv run playwright install chromium`（只需一次）。

## 命令速查

| 命令 | 作用 |
|---|---|
| `uv run ieee-tools search -q "<关键词>" ...` | 检索，写论文清单到 `work/search_results/<查询>/` |
| `uv run ieee-tools export_cookies` | 人工登录，导出 Netscape Cookie |
| `uv run ieee-tools download --jsonl <清单>` | 按清单下载 PDF（默认落到清单同目录，断点续传） |
| `uv run ieee-tools download --arnumber 11617481` | 直连下载（默认落到 `work/papers/`） |
| `uv run ieee-tools status --dir <目录>` | 看进度与失败清单 |

`uv run ieee-tools help` 看全部参数；`uv run ieee-tools <命令> -h` 看单命令参数。

## 标准工作流（四步）

以查询 `Time-Domain ADC` 为例，目录自动为 `work/search_results/Time-Domain_ADC/`（空格→`_`、短横保留）。

**1. 检索**

```bash
cd ~/work/hermes/ieee_tools
uv run ieee-tools search -q "Time-Domain ADC" --save-count 100
```

产物：`work/search_results/Time-Domain_ADC/ieee_papers.jsonl`（下载清单）+ 同目录 `ieee_raw.json`。
⚠️ 至少给一个检索条件（`-q` 或 `--author/--doi/--year/...`），否则报错退出 3。
需要自定义目录时加 `--dir work/<目录>` 覆盖默认。

**2. （可选）确认清单**：查看 `work/search_results/<查询>/ieee_papers.jsonl` 的行数与标题，确认命中主题。

**3. 导出 Cookie（人工步骤）**

```bash
uv run ieee-tools export_cookies
```

**必须由主人本人**在弹出的 Chromium 里登录 IEEE / 连接机构 VPN，确认能打开一篇有权限的论文后，
回终端按 Enter。**Agent 不要代填账号密码、不要猜凭据、不要读取 Cookie 内容。**

**4. 下载**（默认把 PDF 落到清单同目录，无需 `--out-dir`）

```bash
# 先试跑 1 篇验证权限
uv run ieee-tools download --jsonl work/search_results/Time-Domain_ADC/ieee_papers.jsonl --limit 1
# 成功后全量
uv run ieee-tools download --jsonl work/search_results/Time-Domain_ADC/ieee_papers.jsonl
```

默认 `playwright` 引擎；PDF 统一以 arnumber 命名（每篇都有 article_number）。

## 产物约定

- 检索清单 / 原始响应 / 下载状态：`work/search_results/<查询>/ieee_papers.jsonl`、`ieee_raw.json`、`download_state.jsonl`
- 用 `--jsonl` 下载的 PDF：默认落在**该 JSONL 所在目录**（即 `work/search_results/<查询>/`）
- 用 `--arnumber`/`--doi`/`--url` 直连下载的 PDF：默认落在 `work/papers/`
- 任意方式都可用 `--out-dir` 覆盖 PDF 目录

## 长任务模式（几百篇 / 数小时）

下载是长任务（篇间延时 + 限流冷却），**不要让单次工具调用干等**：

1. 后台启动：
   `nohup uv run ieee-tools download --jsonl work/search_results/<查询>/ieee_papers.jsonl > work/search_results/<查询>/download.log 2>&1 &`
2. 轮询：`uv run ieee-tools status --dir work/search_results/<查询> --json`，按 `total / counts / failed` 汇报进度。
3. 结束后失败项：加 `--retry-failed` 再跑一遍。

详见 `references/workflow.md`。

## 出错怎么办

- `AUTH FAILED`（退出 1）→ 重跑 `export_cookies`（或连 VPN）。
- `not_pdf` / HTTP 202 → 风控页，换 `playwright` 引擎 + 刷新 Cookie。
- `rate_limited` → 冷却后用 `--retry-failed`；调大 `--delay`。
- 中断（退出 130）→ 直接重跑同一命令即续传。

详见 `references/troubleshooting.md`。

## 参考

- `references/cli-reference.md` — 全部命令与参数、退出码
- `references/workflow.md` — 四步流程细节、后台轮询模板
- `references/troubleshooting.md` — 常见问题
