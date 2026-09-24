# IEEE Xplore 论文检索、Cookie 导出与批量下载工具

一套面向 IEEE Xplore 的 Python 工具：先通过 **IEEE Xplore Metadata API** 按主题检索论文并保存元数据，再使用 Chromium 辅助登录并导出 **Netscape 格式 Cookie**，最后凭机构订阅权限批量下载全文 PDF。检索与下载支持命令行运行，Cookie 导出会打开一个浏览器窗口供用户完成登录；下载流程支持断点续传、限流退避、PDF 完整性校验，以及 arnumber / DOI 两种论文标识模式。

**注意：本工具不提供绕过IEEE订阅的下载方式，下载受限论文需要你处于可访问受限论文的网络环境内**

---

## 1. 项目简介

本项目包含三个相互衔接的脚本：`ieee_search.py` 和 `ieee_papers_download.py` 通过 JSONL 清单传递论文数据，`export_ieee_cookies.py` 负责准备下载阶段所需的登录凭据。

| 脚本 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `ieee_search.py` | 调用 IEEE Metadata API 分页检索论文，自动去重、自动重试限流 | `.env` 中的 API Key + 文件顶部的检索条件 | `work/ieee_papers.jsonl`（每行一篇完整元数据）、`work/ieee_raw.json`（合并后的原始响应） |
| `export_ieee_cookies.py` | 打开 Playwright Chromium，辅助完成 IEEE Xplore 登录并导出 Cookie | 用户在浏览器中的登录状态和机构订阅权限 | `config/ieee_cookies.txt`（Netscape 格式）、`.cache/ieee-chrome-profile/`（持久化浏览器资料） |
| `ieee_papers_download.py` | 按 JSONL 清单批量下载 PDF | `work/ieee_papers.jsonl` + `config/ieee_cookies.txt` | `papers/*.pdf`、`work/download_state.jsonl`（下载状态） |

主要能力：

- **检索**：支持关键词、作者、DOI、年份、出版物、日期范围等常用 IEEE Metadata API 查询参数；自动分页并按 `article_number`（回退 DOI）跨页去重；遇 HTTP 403 限流自动等待重试。
- **Cookie 导出**：
  - 使用 Playwright 自带的 Chromium 打开 IEEE Xplore，无需额外安装浏览器扩展；
  - 使用持久化用户目录保存登录状态，再次运行时可复用仍有效的会话；
  - 用户确认具有论文访问权限后按 Enter，脚本读取浏览器 Cookie，仅保留域名属于 `ieee.org` 的记录并写出 Netscape 格式文件。
- **下载**：
  - 双引擎：`playwright`（headless Chromium，默认，过风控能力强）与 `httpx`（纯 HTTP，速度快）；
  - 双标识模式：`arnumber`（默认）或 `doi`，DOI 模式优先使用 JSONL 元数据中的 `article_number`，缺失时才经 doi.org（失败自动回退 Crossref）在线解析；
  - 进度行同时展示两种标识，如 `[5] doi:10.1109/JSSC.2026.3693653 arnumber:11526779`，缺失的一侧显示 `null`；
  - 健壮性：随机延时、限流冷却与连续熔断、瞬时错误指数退避重试、PDF 魔数/大小/`%%EOF`/Content-Length 完整性校验、`.part` 临时文件原子写盘；
  - 断点续传：已下载且校验有效的 PDF 自动跳过，状态全部记录在 JSONL 中，可随时中断重跑。

> 注意：下载全文需要有效的机构订阅权限（校园网/VPN 或订阅账号登录状态）。`config/ieee_cookies.txt` 和 `.cache/ieee-chrome-profile/` 都可能包含可代表登录态的敏感信息，请勿提交到 Git 或分享给他人。本工具只负责自动化请求，请遵守 IEEE Xplore 服务条款，合理控制频率，勿用于大规模抓取。

---

## 2. 项目目录

```text
ieee_tools/
├── ieee_search.py                 # 检索脚本：IEEE Metadata API → JSONL
├── export_ieee_cookies.py         # Cookie 导出脚本：浏览器登录 → Netscape 文件
├── ieee_papers_download.py        # 下载脚本：JSONL + Cookie → PDF
├── pyproject.toml                 # 项目与依赖声明（uv）
├── uv.lock                        # 依赖锁定
├── .python-version                # Python 版本（3.12）
├── .env                           # IEEE_API_KEY（需自行创建）
├── config/
│   ├── ieee_cookies.txt           # 导出脚本生成
│   └── ieee_cookies_example.txt   # Cookie 文件格式示例
├── .cache/
│   └── ieee-chrome-profile/       # Playwright 持久化浏览器资料（首次运行时生成）
├── work/                          # 检索产物与下载状态
│   ├── ieee_papers.jsonl          #   论文清单，每行一篇 article 元数据（检索产出）
│   ├── ieee_raw.json              #   合并后的原始 API 响应（留档/排查用）
│   └── download_state.jsonl       #   每篇论文的下载状态（下载时自动生成）
├── papers/                        # PDF 输出目录（自动创建）
└── src/ieee_tools/                # 打包占位；日常直接运行根目录三个脚本即可
```

通常只需要先准备 `.env` 中的 API Key；登录 Cookie 由 `export_ieee_cookies.py` 自动生成。

---

## 3. 快速开始

环境要求：Python ≥ 3.12，推荐使用 [uv](https://docs.astral.sh/uv/)。以下命令均在项目根目录执行。

### 3.1 安装依赖

```powershell
# 在项目根目录
uv sync
uv run playwright install chromium
```

`uv sync` 会安装项目声明的 `httpx` 和 `playwright`。Chromium 内核供 Cookie 导出脚本及默认的 `playwright` 下载引擎使用，只需安装一次。

不用 uv 时也可以创建标准 venv：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install httpx playwright
playwright install chromium
```

### 3.2 配置 IEEE API Key

到 [IEEE Xplore API Portal](https://developer.ieee.org/) 申请 API Key，在项目根目录创建 `.env`：

```dotenv
IEEE_API_KEY=你的apikey
```

### 3.3 检索论文

打开 `ieee_search.py`，修改文件顶部的检索条件，例如把 `querytext` 改成你的关键词。也可以设置 `author`、`d_year`、`publication_title`、`start_date` / `end_date` 等参数；留空的参数不会发送：

```python
querytext = "ADC"
```

运行：

```powershell
uv run python ieee_search.py
```

结束后 `work/ieee_papers.jsonl` 即下载清单，每行是一篇论文的完整元数据（含 `article_number`、`doi`、`title`、`authors`、`abstract` 等）。

### 3.4 导出登录 Cookie

先运行：

```powershell
uv run export_ieee_cookies.py
```

脚本会自动打开 Playwright 自带的 Chromium 并访问 [IEEE Xplore](https://ieeexplore.ieee.org/)。随后：

1. 在打开的 Chromium 中登录 IEEE Xplore；如机构要求校园网或 VPN，请先完成网络连接；
2. 确认当前账号能够打开一篇有下载权限的论文，以验证订阅权限有效；
3. 返回运行脚本的终端，按 Enter；
4. 脚本会过滤并导出 IEEE 相关 Cookie，默认写入 `config/ieee_cookies.txt`。

导出成功时会显示读取的 Cookie 数量、保留的 IEEE Cookie 数量以及输出文件的绝对路径。每次导出都会覆盖 `config/ieee_cookies.txt`；浏览器登录状态则保存在 `.cache/ieee-chrome-profile/`，再次运行时可能无需重新登录。无需安装 `Get cookies.txt` 一类的浏览器扩展。

> `config/ieee_cookies.txt` 和 `.cache/ieee-chrome-profile/` 都属于敏感数据，请勿提交或分享。

### 3.5 批量下载 PDF

```powershell
uv run ieee_papers_download.py                    # 默认 playwright 引擎 + arnumber 模式
uv run ieee_papers_download.py --engine httpx     # 改用更快的纯 HTTP 引擎
uv run ieee_papers_download.py --limit 5          # 本次只处理前 5 篇（试跑推荐）
```

典型输出：

```text
Engine: playwright | id_type: arnumber | items: 5 | out: ...\papers | state: ...\work\download_state.jsonl
[1] doi:10.1109/ISCA66397.2026.00175 arnumber:11617481
    MXFFP: Microscaling Flexible Floating Point Format ...
  OK 1767960 bytes -> 11617481.pdf
```

下载完成后 PDF 位于 `papers/`；中断后直接重跑同一命令即可续传。

---

## 4. 参数配置

`ieee_search.py` 和 `export_ieee_cookies.py` 主要通过文件顶部常量配置；`ieee_papers_download.py` 同时支持文件顶部默认值和命令行覆盖。

### 4.1 `ieee_search.py`（文件顶部常量）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `querytext` | `"ADC"` | 全文检索关键词；与其他检索条件至少填写一个 |
| `author` / `article_title` / `doi` / `d_year` / `publication_title` / `start_date` / `end_date` 等 | `None` | IEEE Metadata API 过滤参数，非空才发送；完整列表见文件内配置区 |
| `start_record` | `1` | 起始记录序号 |
| `max_records` | `200` | 每页记录数，IEEE 限制 1–200 |
| `sort_field` / `sort_order` | `None` | 排序字段与方向 |
| `request_interval_seconds` | `10` | 翻页请求间隔秒数，避免触发 QPS 限流；`0` 表示关闭 |
| `http_403_retry_count` / `http_403_retry_wait_seconds` | `3` / `60` | HTTP 403 限流时的重试次数与等待秒数 |
| `save_count` | `0` | 最多保存多少篇到 JSONL；`0` 表示全部 |
| `PAPERS_JSONL_FILE` / `RAW_JSON_FILE` | `work/ieee_papers.jsonl` / `work/ieee_raw.json` | 输出路径（相对运行目录） |

### 4.2 `export_ieee_cookies.py`（文件顶部常量）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `USER_DATA_DIR` | `Path(".cache/ieee-chrome-profile")` | Playwright Chromium 的持久化用户目录，用于保留登录状态 |
| `OUTPUT` | `Path("config/ieee_cookies.txt")` | Netscape 格式 Cookie 输出路径 |

该脚本没有命令行参数。两个路径均相对当前工作目录解析，因此建议始终在项目根目录运行。

### 4.3 `ieee_papers_download.py`（文件顶部常量）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_INPUT_JSONL` | `work/ieee_papers.jsonl` | 默认论文清单 |
| `DEFAULT_COOKIES` | `config/ieee_cookies.txt` | Netscape Cookie 文件 |
| `PAPERS_DIR` | `papers/` | PDF 输出目录 |
| `ID_TYPE` | `"arnumber"` | 标识模式：`"arnumber"`（PDF 以 arnumber 命名）或 `"doi"`（PDF 以净化后的 DOI 命名） |
| `DEFAULT_ENGINE` | `"playwright"` | 下载引擎：`"playwright"` 或 `"httpx"` |
| `DEFAULT_DELAY_SECONDS` / `DELAY_JITTER_SECONDS` | `20.0` / `3.0` | 篇间基础延时与随机抖动（实际延时约为基准 ± 抖动） |
| `RATE_LIMIT_COOLDOWN_SECONDS` | `1200`（20 分钟） | 遇到限流后的冷却时间 |
| `MAX_CONSECUTIVE_RATE_LIMITS` | `3` | 连续多少次限流后熔断退出 |
| `HTTP_RETRY_COUNT` / `HTTP_RETRY_BACKOFF_MAX` | `3` / `30` | 单篇瞬时错误（超时/5xx/网络）的重试次数与最大退避秒数；401/403/404/非 PDF 等确定性错误不重试 |
| `DOCUMENT_TIMEOUT_SECONDS` / `PDF_TIMEOUT_SECONDS` | `60` / `180` | 文档页与 PDF 请求超时（秒） |
| `MIN_PDF_BYTES` | `10000` | 合法 PDF 的最小体积（IEEE 错误页通常是小体积 HTML） |

### 4.4 `ieee_papers_download.py` 命令行参数

| 参数 | 说明 |
|---|---|
| `--jsonl PATH` | 论文清单，默认 `work/ieee_papers.jsonl` |
| `--out-dir DIR` | PDF 输出目录，默认 `papers/` |
| `--cookies PATH` | Cookie 文件，默认 `config/ieee_cookies.txt` |
| `--state PATH` | 状态文件，默认放在输入 JSONL 同目录下的 `download_state.jsonl` |
| `--engine {playwright,httpx}` | 选择下载引擎 |
| `--delay SECONDS` | 篇间基础延时，`0` 表示不等待（调试用） |
| `--limit N` | 本次最多处理 N 篇，`0` 表示全部 |
| `--retry-failed` | 只处理状态文件中此前非成功的论文 |
| `--force` | 即使本地已有有效 PDF 也重新下载 |
| `--cooldown-seconds N` | 覆盖默认限流冷却时间 |
| `--max-rate-limits N` | 覆盖默认连续限流熔断次数 |

退出码：`0` 成功；`1` 鉴权失败（Cookie/VPN）；`2` 存在失败项或熔断；`3` 参数/文件错误；`130` 被 Ctrl+C 中断。

---

## 5. 常见问题（FAQ）

**Q1：运行检索脚本报 `Please set IEEE_API_KEY in .env first`。**
未找到 API Key。确认根目录 `.env` 中写了 `IEEE_API_KEY=...`（无引号、等号两侧无空格），或已设置同名环境变量。

**Q2：检索时频繁出现 HTTP 403（Service Over Qps）。**
这是 IEEE API 的 QPS 限流，脚本会自动等待重试。若持续失败，可调大 `ieee_search.py` 顶部的 `request_interval_seconds`（例如 30 或更高）和 `http_403_retry_wait_seconds`，稍后再运行。

**Q3：下载一开始就报 `AUTH FAILED`（401/403），或所有论文都失败。**
机构订阅 Cookie 缺失、失效或与当前网络环境不匹配。先连接校园网/VPN，再运行 `uv run python export_ieee_cookies.py` 重新登录并导出 Cookie。可用 `--limit 1` 快速验证。

**Q4：Cookie 导出时提示未检测到 Chromium，或下载时报 `playwright is not installed`。**
先执行 `uv sync` 安装项目依赖，再执行 `uv run playwright install chromium` 下载浏览器内核。若 Cookie 已经有效，也可以在下载时加 `--engine httpx` 使用纯 HTTP 引擎；但重新导出 Cookie 仍需要 Chromium。

**Q5：每次下载前都需要重新导出 Cookie 吗？**
不需要。脚本会复用 `.cache/ieee-chrome-profile/` 中的登录状态，Cookie 未过期且订阅权限仍有效时可以继续使用现有 `config/ieee_cookies.txt`。若出现 `AUTH FAILED`、`not_pdf` 或登录失效提示，再重新运行导出脚本；每次导出都会覆盖旧文件。

**Q6：日志出现 `warning: document page issue (HTTP 202 ...)` 要紧吗？**
通常不要紧。HTTP 202 可能是 IEEE 的 AWS WAF 挑战页，下载脚本会把它作为警告并继续请求 PDF 端点；如果后续仍失败，再结合 Cookie、VPN 和限流状态排查。

**Q7：某篇报 `not_found`（HTTP 404）或下载到 HTML（`not_pdf`）。**
- `not_found` 表示该 arnumber 在 IEEE 侧没有可用全文入口（仅有摘要、撤稿、未入库全文等都可能），属于确定性失败，脚本不会重试；
- `not_pdf` 说明拿到的是 HTML 而非 PDF，通常是风控页或登录失效；更新 Cookie、确认 VPN 状态后用 `--retry-failed` 重跑。

**Q8：下载中断了怎么办？会重复下载吗？**
直接重跑同一命令即可：本地已存在且通过魔数/大小校验的 PDF 会自动跳过，进度记录在 `work/download_state.jsonl`。`--retry-failed` 只重试失败项，`--force` 强制全部重新下载。

**Q9：arnumber 模式和 DOI 模式怎么选？进度行里的 `null` 是什么意思？**
- `arnumber`（默认）：直接用元数据中的 `article_number` 下载，文件名是 arnumber；
- `doi`：文件名使用净化后的 DOI（`/` 转为 `_`）；下载仍优先使用元数据里的 `article_number`，只有元数据缺失时才经 doi.org / Crossref 在线解析；
- 进度行固定同时显示两个标识：`doi:null` 表示该文无 DOI，`arnumber:null` 表示元数据中无 `article_number`（随后会出现 `resolving DOI ...` 解析日志）。

**Q10：运行下载脚本报 `JSONL file not found: work\ieee_papers.jsonl`。**
尚未生成论文清单。先运行 `uv run python ieee_search.py`，或用 `--jsonl` 指定其他清单文件。

