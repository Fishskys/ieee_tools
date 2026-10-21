# 工作流细节

## 目录约定

```text
work/search_results/<查询>/ieee_papers.jsonl   # 检索产出的论文清单
work/search_results/<查询>/ieee_raw.json       # 原始合并响应（排查用）
work/search_results/<查询>/download_state.jsonl # 下载状态（每篇一行，最新为准）
work/search_results/<查询>/download.log        # 后台下载日志（长任务时）
work/search_results/<查询>/*.pdf               # --jsonl 下载的 PDF（默认落在清单同目录）
work/papers/*.pdf                              # 直连（--arnumber/--doi/--url）下载的 PDF
```

`<查询>` 由检索关键词自动生成：**空格转下划线、短横保留**（如 `Time-Domain ADC` → `Time-Domain_ADC`）。
用 `--dir` 可覆盖默认目录；PDF 目录可用 `--out-dir` 覆盖。

## 完整四步

```bash
cd ~/work/hermes/ieee_tools

# 1) 检索（自动建 work/search_results/Time-Domain_ADC/）
uv run ieee-tools search -q "Time-Domain ADC" --save-count 100

# 2) 看清单（可选）
wc -l work/search_results/Time-Domain_ADC/ieee_papers.jsonl

# 3) 导出 Cookie（人工：主人登录后回车）
uv run ieee-tools export_cookies

# 4) 下载（先 1 篇试跑，再全量；PDF 默认落到清单同目录）
uv run ieee-tools download --jsonl work/search_results/Time-Domain_ADC/ieee_papers.jsonl --limit 1
uv run ieee-tools download --jsonl work/search_results/Time-Domain_ADC/ieee_papers.jsonl
```

## 长任务后台轮询模板

```bash
cd ~/work/hermes/ieee_tools
D="work/search_results/Time-Domain_ADC"

# 后台启动（PDF 默认落到 $D）
nohup uv run ieee-tools download \
  --jsonl "$D/ieee_papers.jsonl" \
  > "$D/download.log" 2>&1 &
echo "pid=$!"

# 轮询进度（Agent 每隔一段时间跑一次，向主人汇报）
uv run ieee-tools status --dir "$D" --json

# 失败项重试
uv run ieee-tools download --jsonl "$D/ieee_papers.jsonl" --retry-failed
```

**要点**：
- 下载是长任务，单次调用不要同步干等；用后台 + `status` 轮询。
- 中断（Ctrl+C 或进程被杀）后直接重跑同一命令即续传——已下载且校验有效的 PDF 自动跳过。
- 判"全部完成"：`status --json` 里 `counts.success == total` 或 `failed` 已按需要处理。

## 断点续传与重试语义

| 场景 | 做法 |
|---|---|
| 中断后续传 | 重跑同一命令 |
| 只重试失败项 | `--retry-failed` |
| 强制全部重下 | `--force` |
| 先小批验证权限 | `--limit 1`（或 5） |
