from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ============================================================
# IEEE Xplore Metadata API configuration
# ============================================================

# search.py lives at <repo>/src/ieee_tools/search.py -> parents[2] is repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_key(env_path: Path, key_name: str) -> str:
    """
    Read a KEY=VALUE entry from a .env-style file using only the stdlib.

    Lines may be quoted ("..." or '...'); blank lines and '#' comments are
    skipped. Returns "" when the key is missing.
    """
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == key_name:
            return value.strip().strip('"').strip("'")
    return ""


# Read the key from .env (an already-set environment variable wins, if present).
IEEE_API_KEY = os.environ.get("IEEE_API_KEY") or load_env_key(ENV_FILE, "IEEE_API_KEY")

# Search parameters. All default to empty/None: nothing is searched unless
# the caller supplies at least one parameter via CLI flags.
querytext = ""

abstract = None
affiliation = None
article_number = None
article_title = None
author = None
d_au = None
doi = None
d_publisher = None
d_pubtype = None
d_year = None
end_date = None
facet = None
index_terms = None
isbn = None
issn = None
is_number = None
meta_data = None
publication_id = None
publication_title = None
publication_year = None
start_date = None
thesaurus_terms = None

# Paging / sorting parameters
start_record = 1
max_records = 200
sort_field = None
sort_order = None

# Delay in seconds between page requests, to avoid IEEE API rate
# limiting (HTTP 403 "Service Over Qps"). Set to 0 to disable.
request_interval_seconds = 30

# Automatic retry when the IEEE API responds HTTP 403 ("Service Over
# Qps" rate limiting): wait http_403_retry_wait_seconds and retry up to
# http_403_retry_count times; abort if all retries fail.
http_403_retry_count = 3
http_403_retry_wait_seconds = 60

# Response format
format = "json"


# ============================================================
# Additional configuration
# ============================================================

# 0 = save all results
# >0 = save at most this many papers to the JSONL output
save_count = 0


API_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

# JSONL: one article metadata object per line; the merged raw response is
# saved separately as RAW_JSON_FILE. Anchored to the repo root so behaviour
# does not depend on the current working directory.
WORK_DIR = PROJECT_ROOT / "work"
SEARCH_RESULTS_DIR = WORK_DIR / "search_results"
PAPERS_JSONL_FILE = WORK_DIR / "ieee_papers.jsonl"
RAW_JSON_FILE = WORK_DIR / "ieee_raw.json"


def slugify_query(text: str) -> str:
    """Turn a search query into a directory name.

    Whitespace -> ``_``; hyphens ``-`` are kept; other characters that are
    unsafe in a path (``/ \\ : * ? " < > |`` etc.) become ``_``; repeated
    ``_`` collapse; leading/trailing ``_`` and ``.`` are trimmed. Returns ""
    when nothing usable remains.

    Example: ``"Time-Domain ADC"`` -> ``"Time-Domain_ADC"``.
    """
    s = re.sub(r"\s+", "_", text.strip())
    s = re.sub(r"[^\w.-]+", "_", s)  # keep word chars (incl. CJK), dot, hyphen
    s = re.sub(r"_+", "_", s).strip("_.")
    return s

# Exit codes (aligned with download.py)
EXIT_OK = 0
EXIT_FAILED = 2
EXIT_USAGE = 3


def build_search_params(start: int) -> dict[str, str]:
    """
    Build IEEE Metadata API parameters.

    Only non-empty parameters are sent.
    """
    params = {
        "apikey": IEEE_API_KEY,
        "format": format,
        "start_record": str(start),
        "max_records": str(max_records),
    }

    api_parameters = {
        "querytext": querytext,
        "abstract": abstract,
        "affiliation": affiliation,
        "article_number": article_number,
        "article_title": article_title,
        "author": author,
        "d-au": d_au,
        "doi": doi,
        "d-publisher": d_publisher,
        "d-pubtype": d_pubtype,
        "d-year": d_year,
        "end_date": end_date,
        "facet": facet,
        "index_terms": index_terms,
        "isbn": isbn,
        "issn": issn,
        "is_number": is_number,
        "meta_data": meta_data,
        "publication_id": publication_id,
        "publication_title": publication_title,
        "publication_year": publication_year,
        "start_date": start_date,
        "thesaurus_terms": thesaurus_terms,
    }

    for key, value in api_parameters.items():
        if value is not None and value != "":
            params[key] = str(value)

    if sort_field is not None and sort_field != "":
        params["sort_field"] = str(sort_field)

    if sort_order is not None and sort_order != "":
        params["sort_order"] = str(sort_order)

    return params


def request_page(start: int) -> dict:
    """
    Request one page from IEEE Xplore Metadata API.

    On HTTP 403 (rate limiting) the request is retried up to
    http_403_retry_count times, waiting http_403_retry_wait_seconds
    between attempts; other HTTP/network errors abort immediately.
    """
    params = build_search_params(start)
    url = f"{API_URL}?{urlencode(params)}"

    # Total attempts = 1 initial request + http_403_retry_count retries.
    for attempt in range(http_403_retry_count + 1):
        if attempt > 0:
            print(
                f"IEEE API returned HTTP 403 (rate limited); "
                f"retry {attempt}/{http_403_retry_count} "
                f"in {http_403_retry_wait_seconds:g}s..."
            )
            time.sleep(http_403_retry_wait_seconds)

        # Build a fresh Request for every attempt.
        request = Request(
            url,
            headers={
                "User-Agent": "IEEE-Xplore-Metadata/1.0",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=60) as response:
                data = response.read().decode("utf-8")

        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")

            # Retry only on rate limiting (403) while attempts remain.
            if exc.code == 403 and attempt < http_403_retry_count:
                print(
                    f"IEEE API HTTP 403 (Service Over Qps) on attempt "
                    f"{attempt + 1}/{http_403_retry_count + 1}."
                )
                continue

            if exc.code == 403:
                raise RuntimeError(
                    f"IEEE API HTTP 403 persisted after {http_403_retry_count} "
                    f"retries; aborting.\n{body}"
                ) from exc

            raise RuntimeError(f"IEEE API HTTP {exc.code}\n{body}") from exc

        except URLError as exc:
            raise RuntimeError(f"Failed to connect to IEEE API: {exc.reason}") from exc

        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"IEEE API returned invalid JSON:\n{data[:1000]}"
            ) from exc

    # Unreachable: the final failed attempt raises above.
    raise RuntimeError("IEEE API request failed after all retries.")


def extract_articles(data: dict) -> list[dict]:
    """
    Extract article records from the API response.
    """
    articles = data.get("articles")

    if articles is None:
        return []

    if not isinstance(articles, list):
        raise RuntimeError("Unexpected IEEE API response: 'articles' is not a list.")

    return articles


def article_identity(article: dict) -> str:
    """
    Stable identity for de-duplicating articles across pages:
    article_number first, DOI as fallback; "" when neither is present.
    """
    article_number = str(article.get("article_number") or "").strip()
    if article_number:
        return f"arn:{article_number}"
    doi = str(article.get("doi") or "").strip()
    if doi:
        return f"doi:{doi.lower()}"
    return ""


def _run_search() -> tuple[int, int, int, int]:
    """Run the paged search and write the JSONL / raw JSON outputs.

    Returns (pages_requested, saved, with_doi, without_doi).
    """
    PAPERS_JSONL_FILE.parent.mkdir(parents=True, exist_ok=True)

    saved = 0
    with_doi = 0
    without_doi = 0
    current_record = start_record
    seen_articles: set[str] = set()
    pages_requested = 0
    merged_data: dict | None = None

    with PAPERS_JSONL_FILE.open("w", encoding="utf-8", newline="") as output:
        while True:
            # Wait before every page after the first one to respect rate limits.
            if pages_requested > 0 and request_interval_seconds > 0:
                print(f"Waiting {request_interval_seconds:g}s before next request...")
                time.sleep(request_interval_seconds)

            print(
                f"Requesting IEEE records "
                f"start_record={current_record}, "
                f"max_records={max_records}"
            )

            data = request_page(current_record)
            articles = extract_articles(data)
            pages_requested += 1

            # Merge this page into one combined response: keep the first page's
            # top-level fields and concatenate every page's "articles" list.
            if merged_data is None:
                merged_data = data
                merged_data.setdefault("articles", [])
            else:
                merged_data.setdefault("articles", []).extend(articles)
                for key, value in data.items():
                    if key != "articles" and key not in merged_data:
                        merged_data[key] = value

            if not articles:
                print("No more articles.")
                break

            page_saved = 0

            for article in articles:
                # De-duplicate by article_number (fallback DOI).
                identity = article_identity(article)
                if identity and identity in seen_articles:
                    continue
                if identity:
                    seen_articles.add(identity)

                # One full article metadata object per line (JSONL).
                output.write(json.dumps(article, ensure_ascii=False) + "\n")

                saved += 1
                page_saved += 1
                if article.get("doi"):
                    with_doi += 1
                else:
                    without_doi += 1

                if save_count > 0 and saved >= save_count:
                    break

            output.flush()

            print(
                f"Page returned: {len(articles)}, "
                f"saved: {page_saved}, "
                f"total saved: {saved} "
                f"(with DOI: {with_doi}, without DOI: {without_doi})"
            )

            if save_count > 0 and saved >= save_count:
                break

            if len(articles) < max_records:
                print("Last page reached.")
                break

            current_record += len(articles)

    # Save all pages merged into one formatted JSON file.
    if merged_data is not None:
        RAW_JSON_FILE.write_text(
            json.dumps(merged_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return pages_requested, saved, with_doi, without_doi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ieee-tools search",
        description=(
            "Search IEEE Xplore via the Metadata API and save results to a "
            "JSONL paper list. At least one search parameter is required."
        ),
    )
    parser.add_argument(
        "-q", "--query", default=None,
        help="Full-text query keywords (querytext)",
    )
    parser.add_argument("--author", default=None, help="Author filter")
    parser.add_argument(
        "--title", default=None, help="Article title filter (article_title)"
    )
    parser.add_argument("--doi", default=None, help="DOI filter")
    parser.add_argument("--year", default=None, help="Publication year (d_year)")
    parser.add_argument(
        "--publication", default=None, help="Publication title filter"
    )
    parser.add_argument("--start-date", default=None, help="Start date (YYYYMMDD)")
    parser.add_argument("--end-date", default=None, help="End date (YYYYMMDD)")
    parser.add_argument(
        "--max-records", type=int, default=None,
        help="Records per page, 1-200 (default 200)",
    )
    parser.add_argument(
        "--save-count", type=int, default=None,
        help="Max papers to save, 0 = all (default 0)",
    )
    parser.add_argument(
        "--interval", type=float, default=None,
        help="Seconds between page requests (default 30)",
    )
    parser.add_argument(
        "--retry-403-count", type=int, default=None,
        help="Retries on HTTP 403 rate limiting (default 3)",
    )
    parser.add_argument(
        "--retry-403-wait", type=float, default=None,
        help="Seconds to wait between 403 retries (default 60)",
    )
    parser.add_argument(
        "--dir", dest="out_dir", default=None,
        help="Output directory for ieee_papers.jsonl / ieee_raw.json "
        "(default: work/search_results/<query>, where the query has spaces "
        "turned into underscores)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    global querytext, author, article_title, doi, d_year, publication_title
    global start_date, end_date
    global max_records, save_count, request_interval_seconds
    global http_403_retry_count, http_403_retry_wait_seconds
    global PAPERS_JSONL_FILE, RAW_JSON_FILE

    if args.query is not None:
        querytext = args.query
    if args.author is not None:
        author = args.author
    if args.title is not None:
        article_title = args.title
    if args.doi is not None:
        doi = args.doi
    if args.year is not None:
        d_year = args.year
    if args.publication is not None:
        publication_title = args.publication
    if args.start_date is not None:
        start_date = args.start_date
    if args.end_date is not None:
        end_date = args.end_date
    if args.max_records is not None:
        max_records = args.max_records
    if args.save_count is not None:
        save_count = args.save_count
    if args.interval is not None:
        request_interval_seconds = args.interval
    if args.retry_403_count is not None:
        http_403_retry_count = args.retry_403_count
    if args.retry_403_wait is not None:
        http_403_retry_wait_seconds = args.retry_403_wait
    if args.out_dir is not None:
        out_dir = Path(args.out_dir)
        PAPERS_JSONL_FILE = out_dir / "ieee_papers.jsonl"
        RAW_JSON_FILE = out_dir / "ieee_raw.json"
    else:
        # Default: work/search_results/<query slug>/ (query spaces -> _).
        slug = slugify_query(querytext) or "search"
        out_dir = SEARCH_RESULTS_DIR / slug
        PAPERS_JSONL_FILE = out_dir / "ieee_papers.jsonl"
        RAW_JSON_FILE = out_dir / "ieee_raw.json"

    # --- Validation: fail loudly (nonzero exit) instead of searching blindly ---

    if not IEEE_API_KEY:
        print(
            "error: IEEE API key not found. Set IEEE_API_KEY in .env "
            "(or the environment) first.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if not querytext and not any(
        value not in (None, "")
        for value in (
            abstract,
            affiliation,
            article_number,
            article_title,
            author,
            d_au,
            doi,
            d_publisher,
            d_pubtype,
            d_year,
            end_date,
            facet,
            index_terms,
            isbn,
            issn,
            is_number,
            meta_data,
            publication_id,
            publication_title,
            publication_year,
            start_date,
            thesaurus_terms,
        )
    ):
        print(
            "error: no search parameter given. Provide at least one of "
            "-q/--query, --author, --title, --doi, --year, --publication, "
            "--start-date, --end-date, ...",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if not 1 <= max_records <= 200:
        print("error: --max-records must be between 1 and 200.", file=sys.stderr)
        return EXIT_USAGE
    if start_record < 1:
        print("error: start_record must be >= 1.", file=sys.stderr)
        return EXIT_USAGE
    if save_count < 0:
        print("error: --save-count must be >= 0.", file=sys.stderr)
        return EXIT_USAGE
    if request_interval_seconds < 0:
        print("error: --interval must be >= 0.", file=sys.stderr)
        return EXIT_USAGE
    if not isinstance(http_403_retry_count, int) or http_403_retry_count < 0:
        print(
            "error: --retry-403-count must be an integer >= 0.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if http_403_retry_wait_seconds < 0:
        print("error: --retry-403-wait must be >= 0.", file=sys.stderr)
        return EXIT_USAGE

    try:
        pages_requested, saved, with_doi, without_doi = _run_search()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print()
    print("Finished.")
    print(f"Pages requested: {pages_requested}")
    print(f"Papers saved to JSONL: {saved}")
    print(f"  with DOI: {with_doi}, without DOI: {without_doi}")
    print(f"JSONL: {PAPERS_JSONL_FILE}")
    print(f"Raw JSON: {RAW_JSON_FILE}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
