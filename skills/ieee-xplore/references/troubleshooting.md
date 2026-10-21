# 故障排查

## AUTH FAILED（退出 1，401/403 且非限流）
机构订阅 Cookie 缺失、失效，或与当前网络不匹配。
1. 连接校园网 / 机构 VPN。
2. 重跑 `uv run ieee-tools export_cookies` 重新登录导出。
3. 用 `--limit 1` 快速验证。

## 检索报 HTTP 403（Service Over Qps）
IEEE API 的 QPS 限流，脚本会自动等待重试。持续失败时调大 `--interval`（如 30→60）和
`--retry-403-wait`，稍后再跑。

## `Please set IEEE_API_KEY` / API key not found（退出 3）
`.env` 没有 `IEEE_API_KEY`。让主人到 <https://developer.ieee.org/> 申请后写入仓库根 `.env`
（`IEEE_API_KEY=...`，无引号、等号两侧无空格）。**不要代填或猜测。**

## `not_pdf` 或日志出现 `warning: document page issue (HTTP 202 ...)`
202 往往是 IEEE 的 AWS WAF 挑战页，脚本会作为警告继续请求 PDF。若最终 `not_pdf`：
换 `--engine playwright`、刷新 Cookie、确认 VPN，再用 `--retry-failed` 重跑。

## `not_found`（HTTP 404）
该 arnumber 在 IEEE 侧无可用全文入口（仅摘要/撤稿/未入库），属确定性失败，不重试。

## `rate_limited`
触发限流。冷却 `--cooldown-seconds`（默认 20 分钟）后自动重试同一篇；连续 `--max-rate-limits`
次限流会熔断退出（退出 2）。稍后用 `--retry-failed` 续跑，并调大 `--delay`。

## 中断（退出 130）
进度已存 `download_state.jsonl`，直接重跑同一命令即续传。

## Cookie 导出时提示未检测到 Chromium
`uv run playwright install chromium` 安装内核（只需一次）。已有有效 Cookie 时下载可用
`--engine httpx`，但重新导出 Cookie 仍需要 Chromium。

## 每次下载都要重新导出 Cookie 吗？
不必。脚本复用 `config/ieee_cookies.txt` 与持久化登录；仅当出现 `AUTH FAILED` / `not_pdf` /
登录失效时再导出（每次导出覆盖旧文件）。

## 某篇一直是 `not_pdf` / 202 怎么办
先按上面刷 Cookie + 换 playwright 引擎重试；仍失败的多半是风控对该 IP/会话的策略，可隔一段时间或
换网络再试，或人工下载该篇。
