"""
Download PDFs from IEEE Xplore, using institutional cookies.

Paper list is a JSONL file (one article metadata object per line, as
produced by ieee_search.py -> work/ieee_papers.jsonl). Two identifier
modes are selected with ID_TYPE in the user configuration below:
  - arnumber: download directly by article_number (default)
  - doi:     key and name the PDF by DOI; the arnumber is taken from the
             JSONL metadata when present and otherwise resolved via
             doi.org (Crossref as fallback) first

Two engines share a single download pipeline:
  - httpx:      fast plain-HTTP client
  - playwright: headless Chromium, bypasses IEEE bot checks (default)

Behavior:
  - Existing valid PDFs are skipped by default (resume is always on).
  - Failed / rate-limited / corrupt items are retried on the next run.
  - Use --retry-failed to process only previously failed items.
  - Use --force to re-download everything.

Examples:
  python ieee_papers_download.py
  python ieee_papers_download.py --jsonl work/ieee_papers.jsonl --out-dir papers --limit 5
  python ieee_papers_download.py --jsonl work/ieee_papers.jsonl --engine httpx
  python ieee_papers_download.py --retry-failed
  python ieee_papers_download.py --force
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import re
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import quote

import httpx

# ============================================================
# 用户配置区 —— 需要按需调整的参数都集中在这里，下方代码无需改动
# ============================================================

# --- 目录与文件 -------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[0]
WORK_DIR = PROJECT_ROOT / "work"
CONFIG_DIR = PROJECT_ROOT / "config"
PAPERS_DIR = PROJECT_ROOT / "papers"
DEFAULT_COOKIES = CONFIG_DIR / "ieee_cookies.txt"
# ieee_search.py 产出的论文清单（每行一篇 article 元数据）
DEFAULT_INPUT_JSONL = WORK_DIR / "ieee_papers.jsonl"

# --- 清单标识类型 -------------------------------------------------

# 清单标识类型：
#   "arnumber" —— 直接用 article_number 下载，PDF 以 arnumber 命名（默认）
#   "doi"      —— 用 DOI 下载：优先使用 JSONL 元数据中的 article_number
#                 （缺失时才经 doi.org 重定向定位，失败再回退 Crossref），
#                 PDF 以净化后的 DOI 命名
ID_TYPE = "arnumber"

# --- 下载引擎与限速 ---------------------------------------------

# 下载引擎："playwright"（headless Chromium，过风控，默认）或 "httpx"（快）
DEFAULT_ENGINE = "playwright"
# 每篇之间的基础延时（秒）；实际延时为 base ± DELAY_JITTER_SECONDS，0 表示不等待
DEFAULT_DELAY_SECONDS = 20
DELAY_JITTER_SECONDS = 3.0

# 限流熔断：同一篇遇限流后的冷却时间，以及连续多少次限流响应后终止整个运行
RATE_LIMIT_COOLDOWN_SECONDS = 20 * 60
MAX_CONSECUTIVE_RATE_LIMITS = 3

# 单篇内瞬时错误（HTTP 5xx / 超时 / 网络错误）的重试次数与最大退避秒数；
# 确定性错误（401/403/404/非 PDF）永不重试
HTTP_RETRY_COUNT = 3
HTTP_RETRY_BACKOFF_MAX = 30.0

# 超时（秒）
DOCUMENT_TIMEOUT_SECONDS = 60.0
PDF_TIMEOUT_SECONDS = 180.0

# --- PDF 校验与请求 ---------------------------------------------

# 合法 PDF 必须至少这么大且以 %PDF- 开头（IEEE 错误页是小体积 HTML）
MIN_PDF_BYTES = 10_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DOCUMENT_URL = "https://ieeexplore.ieee.org/document/{arnumber}"
PDF_URL = "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber={arnumber}"
# DOI 模式：doi.org 会 302 到 IEEE document 页，从中提取 arnumber
DOI_RESOLVE_URL = "https://doi.org/{doi}"
# DOI 备用解析通道：Crossref 元数据 API（免 key），resource.primary.URL 指向 IEEE 页面
CROSSREF_API_URL = "https://api.crossref.org/works/{doi}"

# 响应体中标识限流 / 机器人挑战的特征标记（用于非标准状态码场景）
RATE_LIMIT_MARKERS = (
    b"over qps",
    b"rate limit",
    b"too many requests",
    b"service unavailable",
)

# --- 退出码 ------------------------------------------------------

EXIT_OK = 0
EXIT_AUTH_FAILED = 1
EXIT_FAILED = 2
EXIT_USAGE = 3
EXIT_INTERRUPTED = 130


# ============================================================
# State labels and JSONL data models
# ============================================================


class Status(str, Enum):
    """Download-state labels written to the JSONL state file."""

    SUCCESS = "success"
    FAILED = "failed"
    NOT_PDF = "not_pdf"
    NOT_FOUND = "not_found"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class PaperItem:
    """One paper from the input JSONL.

    arnumber mode: arnumber is set, item_key equals it (PDF filename and
    state-file key); doi is informational and kept for display.
    doi mode: doi is set and item_key is a sanitized DOI. The arnumber from
    the JSONL metadata is kept too; only when it is missing is the DOI
    resolved through doi.org at download time.
    """

    item_key: str
    title: str
    arnumber: str | None
    doi: str | None


# ============================================================
# Shared helpers: time, JSONL input/state, PDF files
# ============================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_item_key(raw: str) -> str:
    """Turn an identifier (e.g. a DOI containing '/') into a filesystem-safe
    filename/state key."""
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return key or "unknown"


def load_papers_jsonl(path: Path, id_type: str) -> list[PaperItem]:
    """Load the paper JSONL: one article metadata object per line, as
    produced by ieee_search.py.

    id_type="arnumber" keys items by article_number; id_type="doi" keys
    them by a sanitized DOI. Both identifiers are kept from the metadata
    for display and use; doi mode resolves the DOI at download time only
    for rows whose metadata has no article_number. Rows missing the
    identifier required by the selected mode are skipped with a warning;
    duplicate keys are skipped as well.
    """
    if id_type not in ("arnumber", "doi"):
        raise ValueError(
            f"Unknown ID_TYPE {id_type!r} (expected 'arnumber' or 'doi')"
        )

    items: list[PaperItem] = []
    seen: set[str] = set()
    skipped_missing = 0
    skipped_duplicate = 0
    text = path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(rec, dict):
            raise ValueError(f"Line {line_number} is not a JSON object")

        arnumber = str(
            rec.get("article_number") or rec.get("arnumber") or ""
        ).strip()
        doi = str(rec.get("doi") or "").strip()
        title = str(rec.get("title") or "").strip()

        if id_type == "arnumber":
            if not arnumber.isdigit():
                skipped_missing += 1
                print(
                    f"warning: line {line_number} has no valid "
                    f"article_number, skipped",
                    file=sys.stderr,
                )
                continue
            item_key, arn, doi_value = arnumber, arnumber, (doi or None)
        else:
            if not doi:
                skipped_missing += 1
                print(
                    f"warning: line {line_number} has no DOI, skipped",
                    file=sys.stderr,
                )
                continue
            # DOI 模式同样保留元数据中的 arnumber 供下载与双标识提示；
            # 仅当元数据缺失时才在下载阶段解析 DOI
            metadata_arn = arnumber if arnumber.isdigit() else None
            item_key, arn, doi_value = (
                safe_item_key(doi),
                metadata_arn,
                doi,
            )

        if item_key in seen:
            skipped_duplicate += 1
            continue
        seen.add(item_key)
        items.append(PaperItem(item_key, title, arn, doi_value))

    if skipped_missing:
        print(
            f"warning: skipped {skipped_missing} row(s) without a usable "
            f"{id_type} identifier",
            file=sys.stderr,
        )
    if skipped_duplicate:
        print(
            f"warning: skipped {skipped_duplicate} duplicate row(s)",
            file=sys.stderr,
        )
    return items


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl_state(path: Path) -> dict[str, dict]:
    """Return the latest record per item_key. Corrupt lines are skipped
    with a warning instead of aborting the whole run."""
    if not path.exists():
        return {}
    latest: dict[str, dict] = {}
    skipped = 0
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            print(
                f"warning: skipping corrupt state line {line_number} in {path}",
                file=sys.stderr,
            )
            continue
        key = rec.get("item_key")
        if key:
            latest[key] = rec
    if skipped:
        print(
            f"warning: skipped {skipped} corrupt state line(s) in {path}",
            file=sys.stderr,
        )
    return latest


def is_valid_pdf(path: Path, min_bytes: int = MIN_PDF_BYTES) -> bool:
    if not path.is_file():
        return False
    if path.stat().st_size < min_bytes:
        return False
    with path.open("rb") as f:
        return f.read(5) == b"%PDF-"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically via a .part temp file, so a crash mid-write
    can never leave a truncated PDF that looks complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def sleep_with_countdown(seconds: float, tick_seconds: float = 60.0) -> None:
    """Sleep in slices, printing a countdown once per tick. KeyboardInterrupt
    propagates normally so cooldowns remain cancellable."""
    remaining = float(seconds)
    while remaining > 0:
        print(f"  cooldown {max(1, round(remaining / 60))} min remaining...")
        chunk = min(tick_seconds, remaining)
        time.sleep(chunk)
        remaining -= chunk


# ============================================================
# Errors
# ============================================================


class DownloadError(RuntimeError):
    """Base class for all download failures."""


class RateLimitError(DownloadError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"IEEE rate limit HTTP {status}")


class AuthError(DownloadError):
    """Subscription / login required (401/403 without rate-limit markers)."""


class NotFoundError(DownloadError):
    """HTTP 404 — the arnumber does not exist."""


class NotPdfError(DownloadError):
    """Response body is not a PDF (usually an HTML error/challenge page)."""


class PdfTooSmallError(DownloadError):
    def __init__(self, size: int):
        self.size = size
        super().__init__(f"PDF too small ({size} bytes, min {MIN_PDF_BYTES})")


class PdfTruncatedError(DownloadError):
    """PDF starts correctly but is incomplete (missing %%EOF trailer or
    shorter than the advertised Content-Length)."""


class RetriableDownloadError(DownloadError):
    """Transient failure (5xx, timeout, network) worth retrying."""


# ============================================================
# Response classification / validation
# ============================================================


def classify_http_error(
    status: int, body: bytes, arnumber: str, stage: str
) -> DownloadError:
    """Map an HTTP status (and body) to the appropriate exception type,
    distinguishing rate limiting from genuine authorization failures."""
    head = (body or b"")[:2000].lower()
    snippet = (body or b"")[:200].decode("utf-8", errors="replace")

    if status in (420, 429) or (
        status == 403 and any(marker in head for marker in RATE_LIMIT_MARKERS)
    ):
        return RateLimitError(status)

    if status in (401, 403):
        return AuthError(
            f"HTTP {status} at {stage} for arnumber {arnumber} - "
            f"update ieee_cookies.txt or check VPN. {snippet[:120]}"
        )

    if status == 404:
        return NotFoundError(
            f"HTTP 404 at {stage} for arnumber {arnumber}"
        )

    if 500 <= status < 600:
        return RetriableDownloadError(
            f"HTTP {status} at {stage} for arnumber {arnumber}"
        )

    return DownloadError(
        f"HTTP {status} at {stage} for arnumber {arnumber}: {snippet[:120]}"
    )


def _parse_content_length(raw: str | None) -> int | None:
    """Parse a Content-Length header; None when absent or invalid."""
    if not raw:
        return None
    try:
        length = int(raw)
    except ValueError:
        return None
    return length if length > 0 else None


def validate_pdf_body(
    body: bytes, content_type: str, content_length: int | None = None
) -> None:
    """Raise on a body that is not a usable PDF."""
    head = body[:2000].lower()
    if not body.startswith(b"%PDF-"):
        # A 200 response can still be a rate-limit / challenge HTML page.
        if any(marker in head for marker in RATE_LIMIT_MARKERS):
            raise RateLimitError(200)
        snippet = body[:200].decode("utf-8", errors="replace")
        raise NotPdfError(
            f"Response is not PDF (content-type={content_type}): {snippet}"
        )
    if len(body) < MIN_PDF_BYTES:
        raise PdfTooSmallError(len(body))
    # 完整性校验：尾部 %%EOF 标记 + Content-Length 对照，拦截截断下载。
    if b"%EOF" not in body[-2048:]:
        raise PdfTruncatedError(
            f"PDF missing %EOF trailer (body={len(body)} bytes)"
        )
    if content_length is not None and len(body) < content_length:
        raise PdfTruncatedError(
            f"PDF truncated: got {len(body)} of {content_length} bytes"
        )


_DOCUMENT_ARNUMBER_RE = re.compile(r"/document/(\d+)")
_QUERY_ARNUMBER_RE = re.compile(r"[?&]arnumber=(\d+)")


def extract_arnumber(text: str) -> str | None:
    """Pull the IEEE arnumber out of any URL containing /document/<n> or
    arnumber=<n>; None when absent."""
    for pattern in (_DOCUMENT_ARNUMBER_RE, _QUERY_ARNUMBER_RE):
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


def arnumber_from_crossref(body: bytes) -> str | None:
    """Extract the IEEE arnumber from a Crossref /works/{doi} JSON response
    (resource.primary.URL, link[].URL or URL fields)."""
    try:
        message = json.loads(body).get("message", {})
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    candidates: list[str] = []
    primary = (message.get("resource") or {}).get("primary", {}).get("URL")
    if primary:
        candidates.append(primary)
    for link in message.get("link") or []:
        if isinstance(link, dict) and link.get("URL"):
            candidates.append(link["URL"])
    if message.get("URL"):
        candidates.append(message["URL"])
    for url in candidates:
        arnumber = extract_arnumber(str(url))
        if arnumber:
            return arnumber
    return None


# ============================================================
# Cookies
# ============================================================


def load_cookie_jar(path: Path) -> httpx.Cookies:
    """Load a Netscape-format cookies.txt into httpx cookies."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Cookie file not found: {path}\n"
            "Export Netscape cookies from IEEE Xplore to config/ieee_cookies.txt"
        )
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    cookies = httpx.Cookies()
    for c in jar:
        # 与 netscape_to_playwright 保持一致：去掉 Netscape 文件里域 cookie
        # 的前导点，避免 httpx 域名匹配失效导致 cookie 不发送。
        cookies.set(
            c.name, c.value, domain=(c.domain or "").lstrip("."), path=c.path
        )
    return cookies


def netscape_to_playwright(path: Path) -> list[dict]:
    """Convert a Netscape cookies.txt to Playwright add_cookies dicts,
    keeping only IEEE-domain cookies."""
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    out: list[dict] = []
    for c in jar:
        if "ieee" not in (c.domain or ""):
            continue
        out.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": (c.domain or "").lstrip("."),
                "path": c.path or "/",
                "secure": bool(c.secure),
                "httpOnly": False,
            }
        )
    return out


# ============================================================
# Downloaders (shared template method, two HTTP primitives per engine)
# ============================================================


class BaseDownloader:
    """Common download flow; subclasses implement only the HTTP primitives
    (DOI resolution, document page, PDF bytes) for their engine."""

    engine_name = "base"

    def __enter__(self) -> "BaseDownloader":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        pass

    # -- primitives, implemented per engine -----------------------------

    def http_resolve(self, url: str) -> tuple[int, str, bytes]:
        """GET following redirects; return (status, final_url, body)."""
        raise NotImplementedError

    def http_get_document(self, arnumber: str) -> None:
        raise NotImplementedError

    def http_get_pdf(
        self, arnumber: str
    ) -> tuple[bytes, str, int | None]:
        """Return (pdf_bytes, content_type, content_length); classify all
        failures into the DownloadError hierarchy."""
        raise NotImplementedError

    # -- shared template method -----------------------------------------

    def resolve_arnumber(self, doi: str) -> str:
        """Resolve a DOI to an IEEE arnumber.

        Channel 1 follows doi.org redirects: the final URL is the IEEE
        document page even when IEEE answers with an AWS WAF challenge
        (HTTP 202). Channel 2 queries the Crossref metadata API.
        """
        encoded = quote(doi, safe="/")
        status, final_url, body = self.http_resolve(
            DOI_RESOLVE_URL.format(doi=encoded)
        )
        arnumber = extract_arnumber(final_url)
        if arnumber:
            return arnumber

        crossref_status, crossref_body = 0, b""
        with suppress(DownloadError):
            crossref_status, _, crossref_body = self.http_resolve(
                CROSSREF_API_URL.format(doi=encoded)
            )
        if crossref_status == 200:
            arnumber = arnumber_from_crossref(crossref_body)
            if arnumber:
                return arnumber
        if crossref_status == 404:
            raise NotFoundError(f"DOI not found: {doi} (Crossref 404)")

        if status not in (200, 202):
            raise classify_http_error(
                status, body, f"doi:{doi}", "doi-resolve"
            )
        raise NotFoundError(
            f"could not resolve arnumber for doi:{doi} (landed at {final_url})"
        )

    def open_document(self, arnumber: str) -> None:
        """Visit the abstract page first so the session/cookies are
        established. Only rate limiting, auth and 404 are fatal here;
        timeouts / 5xx warn and the PDF endpoint is attempted anyway."""
        try:
            self.http_get_document(arnumber)
        except (RateLimitError, AuthError, NotFoundError):
            raise
        except DownloadError as exc:
            print(
                f"  warning: document page issue ({exc}); "
                "trying PDF endpoint anyway",
                file=sys.stderr,
            )

    def download(self, item: PaperItem) -> tuple[str, bytes]:
        """Return (arnumber actually used, pdf bytes). In doi mode the
        arnumber is resolved first; failures flow into the same error
        hierarchy as the PDF stage."""
        arnumber = item.arnumber
        if arnumber is None:
            if not item.doi:
                raise DownloadError(
                    f"item {item.item_key} has neither arnumber nor DOI"
                )
            print(f"  resolving DOI {item.doi} ...")
            arnumber = self.resolve_arnumber(item.doi)
            print(f"  DOI resolved -> arnumber {arnumber}")

        self.open_document(arnumber)

        last_error: RetriableDownloadError | None = None
        for attempt in range(HTTP_RETRY_COUNT + 1):
            if attempt > 0:
                backoff = min(
                    float(2 ** (attempt - 1)), HTTP_RETRY_BACKOFF_MAX
                )
                print(
                    f"  transient error: {last_error}; "
                    f"retry {attempt}/{HTTP_RETRY_COUNT} in {backoff:g}s",
                    file=sys.stderr,
                )
                time.sleep(backoff)

            try:
                body, content_type, content_length = self.http_get_pdf(arnumber)
                validate_pdf_body(body, content_type, content_length)
                return arnumber, body
            except (
                RateLimitError,
                AuthError,
                NotFoundError,
                NotPdfError,
                PdfTooSmallError,
                PdfTruncatedError,
            ):
                # Deterministic / handled by the outer loop: no retry.
                raise
            except RetriableDownloadError as exc:
                last_error = exc
            # Other DownloadError types are deterministic as well:
            # propagate immediately without retrying.
            except DownloadError:
                raise

        assert last_error is not None
        raise last_error


class HttpxDownloader(BaseDownloader):
    engine_name = "httpx"

    def __init__(self, cookies_path: Path) -> None:
        cookies = load_cookie_jar(cookies_path)
        self._client = httpx.Client(
            cookies=cookies,
            timeout=httpx.Timeout(PDF_TIMEOUT_SECONDS, connect=30.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        with suppress(Exception):
            self._client.close()

    def _get(
        self, url: str, timeout: float, referer: str | None
    ) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        try:
            return self._client.get(url, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise RetriableDownloadError(f"timeout GET {url}: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetriableDownloadError(f"network error GET {url}: {exc}") from exc

    def http_resolve(self, url: str) -> tuple[int, str, bytes]:
        response = self._get(url, DOCUMENT_TIMEOUT_SECONDS, None)
        return response.status_code, str(response.url), response.content

    def http_get_document(self, arnumber: str) -> None:
        url = DOCUMENT_URL.format(arnumber=arnumber)
        response = self._get(url, DOCUMENT_TIMEOUT_SECONDS, None)
        if response.status_code != 200:
            raise classify_http_error(
                response.status_code, response.content, arnumber, "document"
            )

    def http_get_pdf(self, arnumber: str) -> tuple[bytes, str, int | None]:
        document_url = DOCUMENT_URL.format(arnumber=arnumber)
        pdf_url = PDF_URL.format(arnumber=arnumber)
        response = self._get(
            pdf_url, PDF_TIMEOUT_SECONDS, document_url
        )
        if response.status_code != 200:
            raise classify_http_error(
                response.status_code, response.content, arnumber, "pdf"
            )
        content_type = (response.headers.get("content-type") or "").lower()
        content_length = _parse_content_length(
            response.headers.get("content-length")
        )
        return response.content, content_type, content_length


class PlaywrightDownloader(BaseDownloader):
    engine_name = "playwright"

    def __init__(self, cookies_path: Path) -> None:
        self._cookies_path = cookies_path
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._timeout_error = TimeoutError

    def __enter__(self) -> "PlaywrightDownloader":
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SystemExit(
                "playwright is not installed. Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )
        self._timeout_error = PWTimeout
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=USER_AGENT)
        pw_cookies = netscape_to_playwright(self._cookies_path)
        if pw_cookies:
            self._context.add_cookies(pw_cookies)
        self._page = self._context.new_page()
        return self

    def close(self) -> None:
        with suppress(Exception):
            if self._context is not None:
                self._context.close()
        with suppress(Exception):
            if self._browser is not None:
                self._browser.close()
        with suppress(Exception):
            if self._pw is not None:
                self._pw.stop()

    def http_resolve(self, url: str) -> tuple[int, str, bytes]:
        try:
            response = self._page.request.get(
                url, timeout=int(DOCUMENT_TIMEOUT_SECONDS * 1000)
            )
        except self._timeout_error as exc:
            raise RetriableDownloadError(f"resolve request timeout: {exc}") from exc
        with suppress(Exception):
            return response.status, response.url, response.body()
        return response.status, response.url, b""

    def http_get_document(self, arnumber: str) -> None:
        # Warm up the server-side stamp session with a plain HTTP request.
        # Do NOT navigate a real page here: loading the Xplore SPA via
        # page.goto runs additional XHRs that rotate the session, after
        # which getPDF.jsp is rejected with a redirect to
        # /Xplore/errorpage.jsp (surfaced as HTTP 404). A single document
        # GET through the shared request context keeps the grant (this is
        # also why DOI mode worked: its resolve step performs exactly this
        # request before the navigation can spoil the session).
        url = DOCUMENT_URL.format(arnumber=arnumber)
        try:
            response = self._page.request.get(
                url, timeout=int(DOCUMENT_TIMEOUT_SECONDS * 1000)
            )
        except self._timeout_error as exc:
            raise RetriableDownloadError(f"document page timeout: {exc}") from exc

        if response.status != 200:
            body = b""
            with suppress(Exception):
                body = response.body()
            raise classify_http_error(
                response.status, body, arnumber, "document"
            )

    def http_get_pdf(self, arnumber: str) -> tuple[bytes, str, int | None]:
        document_url = DOCUMENT_URL.format(arnumber=arnumber)
        pdf_url = PDF_URL.format(arnumber=arnumber)
        try:
            response = self._page.request.get(
                pdf_url,
                headers={"Referer": document_url},
                timeout=int(PDF_TIMEOUT_SECONDS * 1000),
            )
        except self._timeout_error as exc:
            raise RetriableDownloadError(f"pdf request timeout: {exc}") from exc

        if response.status != 200:
            try:
                body = response.body()
            except Exception:
                body = b""
            raise classify_http_error(
                response.status, body, arnumber, "pdf"
            )

        body = response.body()
        content_type = (response.headers.get("content-type") or "").lower()
        content_length = _parse_content_length(
            response.headers.get("content-length")
        )
        return body, content_type, content_length


# ============================================================
# Resume / retry decisions
# ============================================================


def should_process(
    item_key: str,
    out_path: Path,
    state: dict[str, dict],
    *,
    retry_failed: bool,
    force: bool,
) -> bool:
    """Decision table for one item:

    force                 -> always process
    valid PDF on disk     -> always skip
    --retry-failed        -> process only items with a non-success record
    default               -> process anything without a valid PDF (this also
                             repairs a 'success' record whose file is missing
                             or corrupt)
    """
    if force:
        return True
    if is_valid_pdf(out_path):
        return False
    previous = state.get(item_key)
    if retry_failed:
        return (
            previous is not None
            and previous.get("status") != Status.SUCCESS.value
        )
    return True


# ============================================================
# Main pipeline (engine-agnostic, written once)
# ============================================================


def run_downloads(
    items: list[PaperItem],
    downloader: BaseDownloader,
    *,
    out_dir: Path,
    state_path: Path,
    delay: float,
    limit: int,
    retry_failed: bool,
    force: bool,
    cooldown_seconds: float,
    max_consecutive_rate_limits: int,
) -> int:
    state = load_jsonl_state(state_path)

    processed = success = skipped = failed = 0
    consecutive_rate_limits = 0

    def summary_line() -> str:
        return (
            f"Processed: {processed}, success: {success}, "
            f"skipped: {skipped}, failed: {failed}"
        )

    for item in items:
        out_path = out_dir / f"{item.item_key}.pdf"

        if not should_process(
            item.item_key,
            out_path,
            state,
            retry_failed=retry_failed,
            force=force,
        ):
            skipped += 1
            continue

        if limit and processed >= limit:
            break

        processed += 1
        # 双标识提示：DOI 与 arnumber 均取自 JSONL 元数据，缺失显示 null
        doi_label = item.doi if item.doi else "null"
        arn_label = item.arnumber if item.arnumber else "null"
        print(f"[{processed}] doi:{doi_label} arnumber:{arn_label}")
        print(f"    {item.title[:80]}")

        record = {
            "item_key": item.item_key,
            "id_type": ID_TYPE,
            "arnumber": item.arnumber,
            "doi": item.doi,
            "title": item.title,
            "ts": utc_now_iso(),
            "engine": downloader.engine_name,
        }

        try:
            while True:
                try:
                    arnumber, body = downloader.download(item)
                    atomic_write_bytes(out_path, body)
                    record.update(
                        {
                            "status": Status.SUCCESS.value,
                            "arnumber": arnumber,
                            "path": str(out_path),
                            "bytes": len(body),
                        }
                    )
                    success += 1
                    consecutive_rate_limits = 0
                    print(f"  OK {len(body)} bytes -> {out_path.name}")
                    break

                except RateLimitError as exc:
                    consecutive_rate_limits += 1
                    print(
                        f"  RATE LIMITED: HTTP {exc.status}; "
                        f"consecutive={consecutive_rate_limits}/"
                        f"{max_consecutive_rate_limits}",
                        file=sys.stderr,
                    )

                    if consecutive_rate_limits >= max_consecutive_rate_limits:
                        record.update(
                            {
                                "status": Status.RATE_LIMITED.value,
                                "error": (
                                    f"HTTP {exc.status}; circuit breaker "
                                    f"after {consecutive_rate_limits} "
                                    "consecutive rate limits"
                                ),
                            }
                        )
                        failed += 1
                        append_jsonl(state_path, record)
                        print(
                            "  CIRCUIT BREAKER: stopping download. "
                            "Re-run later to resume.",
                            file=sys.stderr,
                        )
                        print(f"\n{summary_line()}")
                        return EXIT_FAILED

                    sleep_with_countdown(cooldown_seconds)
                    # Retry the same item after cooling down.

                except AuthError as exc:
                    record.update(
                        {"status": Status.AUTH_FAILED.value, "error": str(exc)}
                    )
                    append_jsonl(state_path, record)
                    print(f"  AUTH FAILED: {exc}", file=sys.stderr)
                    failed += 1
                    print(f"\n{summary_line()}")
                    print(
                        "Stopped due to auth failure. Re-export "
                        "config/ieee_cookies.txt (or connect the VPN) and retry.",
                        file=sys.stderr,
                    )
                    return EXIT_AUTH_FAILED

                except DownloadError as exc:
                    if isinstance(exc, NotFoundError):
                        status = Status.NOT_FOUND
                    elif isinstance(exc, NotPdfError):
                        status = Status.NOT_PDF
                    else:
                        # PdfTooSmallError, exhausted retriable errors, etc.
                        status = Status.FAILED
                    record.update(
                        {"status": status.value, "error": str(exc)}
                    )
                    failed += 1
                    consecutive_rate_limits = 0
                    print(f"  FAIL ({status.value}): {exc}", file=sys.stderr)
                    break

            # Record is finalized (success or deterministic failure).
            append_jsonl(state_path, record)

            # Randomized inter-item delay; delay=0 means no waiting at all.
            if delay > 0:
                wait = random.uniform(
                    max(0.0, delay - DELAY_JITTER_SECONDS),
                    delay + DELAY_JITTER_SECONDS,
                )
                print(f"  Sleep {wait:.1f}s")
                time.sleep(wait)

        except KeyboardInterrupt:
            # No status yet -> interrupted mid-download; otherwise the record
            # was already written and Ctrl+C landed during the inter-item wait.
            if "status" not in record:
                record.update(
                    {
                        "status": Status.INTERRUPTED.value,
                        "error": "interrupted by user",
                    }
                )
                append_jsonl(state_path, record)
            print(
                "\nInterrupted; progress saved. Re-run to resume.",
                file=sys.stderr,
            )
            print(f"\n{summary_line()}")
            return EXIT_INTERRUPTED

    print(f"\n{summary_line()}")
    return EXIT_OK if failed == 0 else EXIT_FAILED


# ============================================================
# CLI
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download PDFs from IEEE Xplore from a JSONL paper list "
            "(ID_TYPE selects arnumber or doi mode)."
        )
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=DEFAULT_INPUT_JSONL,
        help="Paper JSONL, one article object per line "
        "(default: work/ieee_papers.jsonl)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PAPERS_DIR,
        help="Output directory for PDFs (default: papers/)",
    )
    parser.add_argument(
        "--cookies", type=Path, default=DEFAULT_COOKIES,
        help="Netscape cookies.txt (default: config/ieee_cookies.txt)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Download state JSONL (default: next to the input JSONL, "
        "download_state.jsonl)",
    )
    parser.add_argument(
        "--engine",
        choices=("httpx", "playwright"),
        default=DEFAULT_ENGINE,
        help="Download engine (playwright bypasses bot checks)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Base delay between items; actual delay is base +/- 3s (0=off)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max items to process this run (0=all)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Process only items with a previous non-success record",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a valid PDF already exists",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=RATE_LIMIT_COOLDOWN_SECONDS,
        help="Wait after a rate-limit response before retrying",
    )
    parser.add_argument(
        "--max-rate-limits",
        type=int,
        default=MAX_CONSECUTIVE_RATE_LIMITS,
        help="Consecutive rate-limit responses that abort the run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validation
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.cooldown_seconds < 0:
        parser.error("--cooldown-seconds must be >= 0")
    if args.max_rate_limits < 1:
        parser.error("--max-rate-limits must be >= 1")
    if not args.jsonl.is_file():
        parser.error(f"JSONL file not found: {args.jsonl}")

    # Cookies are mandatory for both engines.
    if not args.cookies.is_file():
        print(
            f"Cookie file not found: {args.cookies}\n"
            "Export Netscape cookies from IEEE Xplore to config/ieee_cookies.txt",
            file=sys.stderr,
        )
        return EXIT_USAGE

    items = load_papers_jsonl(args.jsonl, ID_TYPE)
    if not items:
        print(f"No usable items found in {args.jsonl} (id_type={ID_TYPE})")
        return EXIT_OK

    state_path = args.state or (args.jsonl.parent / "download_state.jsonl")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    downloader_cls = (
        PlaywrightDownloader
        if args.engine == "playwright"
        else HttpxDownloader
    )

    print(
        f"Engine: {args.engine} | id_type: {ID_TYPE} | items: {len(items)} | "
        f"out: {args.out_dir} | state: {state_path}"
    )

    with downloader_cls(args.cookies) as downloader:
        return run_downloads(
            items,
            downloader,
            out_dir=args.out_dir,
            state_path=state_path,
            delay=args.delay,
            limit=args.limit,
            retry_failed=args.retry_failed,
            force=args.force,
            cooldown_seconds=args.cooldown_seconds,
            max_consecutive_rate_limits=args.max_rate_limits,
        )


if __name__ == "__main__":
    raise SystemExit(main())
