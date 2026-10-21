# IEEE Xplore 论文检索、Cookie 导出与批量下载工具

一套面向 IEEE Xplore 的 Python 工具：先通过 **IEEE Xplore Metadata API** 按主题检索论文并保存元数据，再使用 Chromium 辅助登录并导出 **Netscape 格式 Cookie**，最后凭机构订阅权限批量下载全文 PDF。检索与下载支持命令行运行，Cookie 导出会打开一个浏览器窗口供用户完成登录；下载流程支持断点续传、限流退避、PDF 完整性校验，PDF 统一以 arnumber 命名。

**注意：本工具不提供绕过IEEE订阅的下载方式，下载受限论文需要你处于可访问受限论文的网络环境内**

---

## 0. 命令行入口（推荐）

所有功能收敛到统一命令 `ieee-tools`（在项目根目录用 `uv run` 执行）：

```bash
uv run ieee-tools            # 基础命令列表
uv run ieee-tools help       # 全命令与全部参数（也支持 -h / --help）
uv run ieee-tools -v         # 版本号（也支持 --version / version）
```

| 命令 | 作用 |
|---|---|
| `ieee-tools search` | 检索 IEEE Xplore，论文清单存到 `work/search_results/<查询>/` |
| `ieee-tools export_cookies` | 打开 Chromium 登录并导出 Netscape Cookie（需人工登录） |
| `ieee-tools download` | 按清单 / 直连下载 PDF（分类到 JSONL 目录或 `work/papers/`） |
| `ieee-tools status` | 查看某目录的下载进度与失败清单 |

单条命令的帮助：`uv run ieee-tools <命令> -h`（或 `--help`）。

> 所有命令都在 `src/ieee_tools/` 包内按命令拆分（`search.py` / `cookies.py` / `download.py`），`cli.py` 只做命令分发；在项目根目录 `uv run ieee-tools <命令>` 即可，无需记住脚本路径。

---

## 1. 项目简介

本项目是一个按命令拆分的 Python 包 `ieee_tools`（位于 `src/ieee_tools/`）：`search.py` 负责检索、`cookies.py` 导出登录 Cookie、`download.py` 负责下载与 `status`，`cli.py` 作为统一入口只做命令分发。检索与下载通过 JSONL 清单衔接。

| 模块 | 作用 | 输入 | 输出 |
|---|---|---|---|
| `src/ieee_tools/search.py` | 调用 IEEE Metadata API 分页检索论文，自动去重、自动重试限流 | `.env` 中的 API Key + 命令行/顶部常量检索条件 | `work/search_results/<查询>/ieee_papers.jsonl`（每行一篇完整元数据）、`work/search_results/<查询>/ieee_raw.json`（合并后的原始响应） |
| `src/ieee_tools/cookies.py` | 打开 Playwright Chromium，辅助完成 IEEE Xplore 登录并导出 Cookie | 用户在浏览器中的登录状态和机构订阅权限 | `config/ieee_cookies.txt`（Netscape 格式）、`.cache/ieee-chrome-profile/`（持久化浏览器资料） |
| `src/ieee_tools/download.py` | 按 JSONL 清单 / `--arnumber`/`--doi`/`--url` 下载 PDF，并提供 `status` | `work/search_results/<查询>/ieee_papers.jsonl` + `config/ieee_cookies.txt` | `--jsonl` 时落到该 JSONL 所在目录；直连时落到 `work/papers/*.pdf`；状态在同目录 `download_state.jsonl` |

主要能力：

- **检索**：支持关键词、作者、DOI、年份、出版物、日期范围等常用 IEEE Metadata API 查询参数；自动分页并按 `article_number`（回退 DOI）跨页去重；遇 HTTP 403 限流自动等待重试。默认按查询词建子目录：`work/search_results/<查询>/`，查询中的空格转下划线、短横保留（如 `Time-Domain ADC` → `Time-Domain_ADC`）。
- **Cookie 导出**：
  - 使用 Playwright 自带的 Chromium 打开 IEEE Xplore，无需额外安装浏览器扩展；
  - 使用持久化用户目录保存登录状态，再次运行时可复用仍有效的会话；
  - 用户确认具有论文访问权限后按 Enter，脚本读取浏览器 Cookie，仅保留域名属于 `ieee.org` 的记录并写出 Netscape 格式文件。
- **下载**：
  - PDF 存放分类：用 `--jsonl` 下载时，默认落到**该 JSONL 文件所在目录**；用 `--arnumber`/`--doi`/`--url` 等直连方式下载时，默认落到 `work/papers/`（均可用 `--out-dir` 覆盖）；
  - 双引擎：`playwright`（headless Chromium，默认，过风控能力强）与 `httpx`（纯 HTTP，速度快）；
  - 统一按 arnumber 命名：每篇 IEEE 论文都有 `article_number`，PDF 以 arnumber 命名；`--doi` 直连会先把 DOI 解析成 arnumber（doi.org，失败回退 Crossref）再下载；
  - 进度行同时展示两种标识，如 `[5] doi:10.1109/JSSC.2026.3693653 arnumber:11526779`，缺失的一侧显示 `null`；
  - 健壮性：随机延时、限流冷却与连续熔断、瞬时错误指数退避重试、PDF 魔数/大小/`%%EOF`/Content-Length 完整性校验、`.part` 临时文件原子写盘；
  - 断点续传：已下载且校验有效的 PDF 自动跳过，状态全部记录在 JSONL 中，可随时中断重跑。

> 注意：下载全文需要有效的机构订阅权限（校园网/VPN 或订阅账号登录状态）。`config/ieee_cookies.txt` 和 `.cache/ieee-chrome-profile/` 都可能包含可代表登录态的敏感信息，请勿提交到 Git 或分享给他人。本工具只负责自动化请求，请遵守 IEEE Xplore 服务条款，合理控制频率，勿用于大规模抓取。

---

## 2. 项目目录

```text
ieee_tools/
├── pyproject.toml                 # 项目与依赖声明（uv）
├── uv.lock                        # 依赖锁定
├── .python-version                # Python 版本（3.12）
├── .env.example                   # IEEE_API_KEY 示例（复制为 .env 后填写）
├── .env                           # IEEE_API_KEY（需自行创建，不入库）
├── README.md / CHANGELOG.md / AGENTS.md   # 用户手册 / 变更记录 / Agent 规范
├── config/
│   ├── ieee_cookies.txt           # 导出脚本生成
│   └── ieee_cookies_example.txt   # Cookie 文件格式示例
├── .cache/
│   └── ieee-chrome-profile/       # Playwright 持久化浏览器资料（首次运行时生成）
├── work/                          # 全部检索产物、下载的 PDF 与状态
│   ├── ieee_raw_example.json      #   原始 API 响应样例（保留入库）
│   ├── ieee_papers_example.jsonl  #   论文清单样例：2 条真实记录（有/无 DOI 各一，保留入库）
│   ├── search_results/            #   检索结果，按查询词分子目录
│   │   └── <查询>/                #     如 Time-Domain_ADC/
│   │       ├── ieee_papers.jsonl  #       论文清单，每行一篇 article 元数据
│   │       └── ieee_raw.json      #       合并后的原始 API 响应（留档/排查）
│   ├── papers/                    #   直连下载（--arnumber/--doi/--url）的 PDF 输出
│   └── download_state.jsonl       #   （旧版/直连）下载状态，默认与对应清单同目录
├── skills/ieee-xplore/            # 面向 Agent 的仓库内 Skill 与 CLI 参考
├── tests/                         # pytest 单元测试（不联网、不起浏览器）
└── src/ieee_tools/
    ├── __init__.py               # 包元数据与版本号
    ├── cli.py                    # ieee-tools 统一入口（顶层命令分发，无业务逻辑）
    ├── search.py                 # ieee-tools search：IEEE Metadata API → JSONL
    ├── cookies.py                # ieee-tools export_cookies：浏览器登录 → Netscape 文件
    └── download.py               # ieee-tools download / status：JSONL+Cookie → PDF、读状态
```

通常只需要先准备 `.env` 中的 API Key；登录 Cookie 由 `ieee-tools export_cookies` 自动生成。

---

## 3. 快速开始

环境要求：Python ≥ 3.12，推荐使用 [uv](https://docs.astral.sh/uv/)。以下命令均在项目根目录执行。

### 3.1 安装依赖

```bash
# 在项目根目录
uv sync
uv run playwright install chromium
```

`uv sync` 会安装项目声明的 `httpx` 和 `playwright`。Chromium 内核供 Cookie 导出脚本及默认的 `playwright` 下载引擎使用，只需安装一次。

不用 uv 时也可以创建标准 venv。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install httpx playwright
playwright install chromium
```

Linux / macOS（bash）：

```bash
python -m venv .venv
source .venv/bin/activate
pip install httpx playwright
playwright install chromium
```

### 3.2 配置 IEEE API Key

到 [IEEE Xplore API Portal](https://developer.ieee.org/) 申请 API Key，在项目根目录创建 `.env`：

```dotenv
IEEE_API_KEY=你的apikey
```

### 3.3 检索论文

用 `search` 子命令检索，`-q/--query` 为关键词；还可加 `--author`、`--title`、`--doi`、`--year`、`--publication`、`--start-date` / `--end-date` 等过滤，留空的参数不会发送：

```bash
uv run ieee-tools search -q "ADC"
uv run ieee-tools search -q "Time-Domain ADC" --year 2025 --save-count 50
uv run ieee-tools search -q "ADC" --dir work/my_adc          # 显式指定输出目录（覆盖默认）
```

**至少必须给一个检索条件**（`-q` 或其它过滤参数），否则直接报错退出——源码里已无默认关键词。

默认把产物写到 `work/search_results/<查询>/`：查询词里的**空格转下划线、短横保留**（如 `Time-Domain ADC` → `work/search_results/Time-Domain_ADC/`）；`--dir` 可显式覆盖。目录内 `ieee_papers.jsonl` 即下载清单，每行是一篇论文的完整元数据（含 `article_number`、`doi`、`title`、`authors`、`abstract` 等）；同目录还有 `ieee_raw.json`（合并后的原始响应）。

### 3.4 导出登录 Cookie

先运行：

```bash
uv run ieee-tools export_cookies
```

（输出路径可用 `--output` 覆盖，`--headless` 用于已有登录态的无头场景。）

脚本会自动打开 Playwright 自带的 Chromium 并访问 [IEEE Xplore](https://ieeexplore.ieee.org/)。随后：

1. 在打开的 Chromium 中登录 IEEE Xplore；如机构要求校园网或 VPN，请先完成网络连接；
2. 确认当前账号能够打开一篇有下载权限的论文，以验证订阅权限有效；
3. 返回运行脚本的终端，按 Enter；
4. 脚本会过滤并导出 IEEE 相关 Cookie，默认写入 `config/ieee_cookies.txt`。

导出成功时会显示读取的 Cookie 数量、保留的 IEEE Cookie 数量以及输出文件的绝对路径。每次导出都会覆盖 `config/ieee_cookies.txt`；浏览器登录状态则保存在 `.cache/ieee-chrome-profile/`，再次运行时可能无需重新登录。无需安装 `Get cookies.txt` 一类的浏览器扩展。

> `config/ieee_cookies.txt` 和 `.cache/ieee-chrome-profile/` 都属于敏感数据，请勿提交或分享。

### 3.5 批量下载 PDF

```bash
# 用自带示例清单试跑（work/ieee_papers_example.jsonl，2 条真实记录，无需先检索）
uv run ieee-tools download --jsonl work/ieee_papers_example.jsonl --limit 1 --out-dir work/try
# 用清单下载：PDF 默认落到该 JSONL 所在目录（work/search_results/<查询>/）
uv run ieee-tools download --jsonl work/search_results/ADC/ieee_papers.jsonl
uv run ieee-tools download --jsonl work/search_results/ADC/ieee_papers.jsonl --engine httpx
uv run ieee-tools download --jsonl work/search_results/ADC/ieee_papers.jsonl --limit 5
# 直连下载：PDF 默认落到 work/papers/
uv run ieee-tools download --arnumber 11617481,11526779           # 逗号分隔多个
uv run ieee-tools download --doi 10.1109/JSSC.2026.3693653,10.1109/ISCA66397.2026.00175
uv run ieee-tools download --url https://ieeexplore.ieee.org/document/11617481
# 任意方式都可用 --out-dir 覆盖 PDF 目录
uv run ieee-tools download --arnumber 11617481 --out-dir work/somewhere
```

> 论文清单的字段格式可参考仓库自带的 `work/ieee_papers_example.jsonl`：每行一个 article 对象，**下载只强依赖 `article_number`**（缺失该字段的行会被跳过），`doi`/`title` 等为可选辅助字段。

典型输出：

```text
Engine: playwright | items: 5 | out: ...\work\search_results\ADC | state: ...\work\search_results\ADC\download_state.jsonl
[1] doi:10.1109/ISCA66397.2026.00175 arnumber:11617481
    MXFFP: Microscaling Flexible Floating Point Format ...
  OK 1767960 bytes -> 11617481.pdf
```

PDF 存放规则：`--jsonl` 下载落到该 JSONL 所在目录；`--arnumber`/`--doi`/`--url` 直连落到 `work/papers/`（均可用 `--out-dir` 覆盖）。中断后直接重跑同一命令即可续传。

---

## 4. 参数配置

各子命令的参数均在命令行传入（`uv run ieee-tools <命令> -h` 查看完整列表）；`download` 命令另保留模块顶部默认值（`src/ieee_tools/download.py`），仅在不传对应参数时生效。

### 4.1 `ieee-tools search`（命令行参数）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-q, --query` | 空 | 全文检索关键词；**与其他过滤参数至少填一个，否则报错退出** |
| `--author` / `--title` / `--doi` / `--year` / `--publication` | 空 | 常用过滤，非空才发送 |
| `--start-date` / `--end-date` | 空 | 日期范围（YYYYMMDD） |
| `--max-records` | `200` | 每页记录数，IEEE 限制 1–200 |
| `--save-count` | `0` | 最多保存多少篇到 JSONL；`0` 表示全部 |
| `--interval` | `30` | 翻页请求间隔秒数，避免 QPS 限流；`0` 表示关闭 |
| `--retry-403-count` / `--retry-403-wait` | `3` / `60` | HTTP 403 限流重试次数与等待秒数 |
| `--dir DIR` | `work/search_results/<查询>/` | 产物目录，写 `ieee_papers.jsonl` + `ieee_raw.json`；查询空格转下划线、短横保留 |

> 更冷门的 Metadata API 过滤参数（abstract/affiliation/isbn/issn 等）仍可在 `src/ieee_tools/search.py` 顶部常量中设置，非空才发送。

### 4.2 `ieee-tools export_cookies`（命令行参数）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--output` | `config/ieee_cookies.txt` | Netscape 格式 Cookie 输出路径 |
| `--headless` | 关 | 无头运行 Chromium（仅在已有登录态时有用） |

Chromium 持久化用户目录由 `src/ieee_tools/cookies.py` 的常量 `USER_DATA_DIR`（`.cache/ieee-chrome-profile`）指定，用于保留登录状态。

### 4.3 `download` 模块顶部常量（`src/ieee_tools/download.py`）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_INPUT_JSONL` | `work/ieee_papers.jsonl` | 默认论文清单 |
| `DEFAULT_COOKIES` | `config/ieee_cookies.txt` | Netscape Cookie 文件 |
| `PAPERS_DIR` | `work/papers` | 直连下载（非 `--jsonl`）的默认 PDF 输出目录 |
| `DEFAULT_ENGINE` | `"playwright"` | 下载引擎：`"playwright"` 或 `"httpx"` |
| `DEFAULT_DELAY_SECONDS` / `DELAY_JITTER_SECONDS` | `20.0` / `3.0` | 篇间基础延时与随机抖动（实际延时约为基准 ± 抖动） |
| `RATE_LIMIT_COOLDOWN_SECONDS` | `1200`（20 分钟） | 遇到限流后的冷却时间 |
| `MAX_CONSECUTIVE_RATE_LIMITS` | `3` | 连续多少次限流后熔断退出 |
| `HTTP_RETRY_COUNT` / `HTTP_RETRY_BACKOFF_MAX` | `3` / `30` | 单篇瞬时错误（超时/5xx/网络）的重试次数与最大退避秒数；401/403/404/非 PDF 等确定性错误不重试 |
| `DOCUMENT_TIMEOUT_SECONDS` / `PDF_TIMEOUT_SECONDS` | `60` / `180` | 文档页与 PDF 请求超时（秒） |
| `MIN_PDF_BYTES` | `10000` | 合法 PDF 的最小体积（IEEE 错误页通常是小体积 HTML） |

### 4.4 `ieee-tools download` 命令行参数

| 参数 | 说明 |
|---|---|
| `--jsonl PATH` | 论文清单，默认 `work/ieee_papers.jsonl`；给出 `--arnumber`/`--doi`/`--url` 时忽略 |
| `--arnumber N[,N...]` | 直接按 arnumber 下载，逗号分隔多个；与 `--doi` 可混用 |
| `--doi DOI[,DOI...]` | 直接按 DOI 下载，逗号分隔多个；先解析成 arnumber 再下载 |
| `--url URL[,URL...]` | 直接按 IEEE 链接下载，逗号分隔多个；自动从链接提取 arnumber |
| `--out-dir DIR` | PDF 输出目录。`--jsonl` 下载默认落到该 JSONL 所在目录；直连下载默认 `work/papers/` |
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

### 4.5 `ieee-tools status` 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--dir DIR` | — | 主题目录（读取其中的 `download_state.jsonl`） |
| `--state PATH` | `work/download_state.jsonl` | 直接指定状态文件路径 |
| `--json` | 关 | 输出机器可读 JSON（`path`/`total`/`counts`/`failed`） |

`status` 以 `download_state.jsonl` 中每篇的**最新一条记录**为准，统计各状态数量并列出非成功项（`item_key` / 状态 / 错误 / 标题）。常用于长任务轮询：`ieee-tools status --dir work/search_results/<查询> --json`。

---

## 5. 常见问题（FAQ）

**Q1：运行检索脚本报 `Please set IEEE_API_KEY in .env first`。**
未找到 API Key。确认根目录 `.env` 中写了 `IEEE_API_KEY=...`（无引号、等号两侧无空格），或已设置同名环境变量。

**Q2：检索时频繁出现 HTTP 403（Service Over Qps）。**
这是 IEEE API 的 QPS 限流，脚本会自动等待重试。若持续失败，可调大 `src/ieee_tools/search.py` 顶部的 `request_interval_seconds`（例如 30 或更高）和 `http_403_retry_wait_seconds`，稍后再运行。

**Q3：下载一开始就报 `AUTH FAILED`（401/403），或所有论文都失败。**
机构订阅 Cookie 缺失、失效或与当前网络环境不匹配。先连接校园网/VPN，再运行 `uv run ieee-tools export_cookies` 重新登录并导出 Cookie。可用 `--limit 1` 快速验证。

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
直接重跑同一命令即可：本地已存在且通过魔数/大小校验的 PDF 会自动跳过，进度记录在清单同目录的 `download_state.jsonl`。`--retry-failed` 只重试失败项，`--force` 强制全部重新下载。

**Q9：PDF 文件名是怎么定的？进度行里的 `null` 是什么意思？**
- PDF 统一以 arnumber 命名（每篇 IEEE 论文都有 `article_number`）；
- `--doi` 直连时先把 DOI 解析成 arnumber（doi.org，失败回退 Crossref），再以该 arnumber 命名下载；
- 进度行同时显示两个标识：`doi:null` 表示该文无 DOI；`arnumber:null` 只出现在 `--doi` 直连解析前的瞬间（解析出 arnumber 后即按 arnumber 命名）。

**Q10：运行下载脚本报 `JSONL file not found: work\ieee_papers.jsonl`。**
尚未生成论文清单。先运行 `uv run ieee-tools search -q "<关键词>"`，或用 `--jsonl` 指定其他清单文件。

**Q11：`ieee-tools status` 显示 `total: 0` 或报 `state file not found`。**
说明该目录还没有下载记录：`download_state.jsonl` 只在 `download` 运行时生成。先用 `--dir` 指到正确的主题目录，或确认 `download` 至少跑过一次（`--state` 可显式指定状态文件）。

---

## 6. 版本与变更记录

变更历史见 [CHANGELOG.md](CHANGELOG.md)（遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本）。

## 7. 开发与测试

单元测试用 pytest，位于 `tests/`，**不联网、不起浏览器**（外部下载/HTTP 用替身）：

```bash
uv sync --dev        # 安装开发依赖（pytest）
uv run pytest        # 运行全部单元测试
uv run pytest -q tests/test_download.py   # 只跑某一文件
```

覆盖点：`search` 的查询→目录名与参数校验、`download` 的清单加载/直连解析/输出目录解析/`status` 统计、
`cookies` 的 Netscape 写出与惰性导入、`cli` 的顶层三态与命令分发。

### 7.1 提交前敏感信息检查（pre-commit）

本项目通过 [pre-commit](https://pre-commit.com) 集成 [init_your_proj](https://github.com/Fishskys/init_your_proj) 的 `iyp-check`：**每次 `git commit` 前只扫描暂存文件**，命中密钥/Cookie/IP/本机用户名等敏感内容即阻断提交。

- 配置：[`.pre-commit-config.yaml`](.pre-commit-config.yaml)（`rev: v0.1.1`，`id: iyp-check`）
- 前置：`iyp` 需在 PATH（`pre-commit` 以 `language: system` 直接调用）
- 安装：`pre-commit install`（写入 `.git/hooks/pre-commit`）；手动全量：`pre-commit run --all-files`

**误报白名单**放在项目根的 `init_your_proj.toml` 的 `[check].whitelist_strings`（子串匹配，命中行含该串即不报）。该文件按惯例**本地维护、不入库**（已在 `.gitignore` 中忽略，与 paperbox 等仓库一致）；当前登记的非敏感字面量：日志文案 `读取 Cookie:` / `IEEE Cookie:`（被 `cookie_header` 误判）、UA 串 `Chrome/120.0.0.0`（被 `ipv4` 误判）。新环境需自行按同样内容创建。

