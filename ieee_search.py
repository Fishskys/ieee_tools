from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ============================================================
# IEEE Xplore Metadata API configuration
# ============================================================

ENV_FILE = Path(__file__).resolve().parent / ".env"


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

# Search parameters
querytext = "ADC"

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
request_interval_seconds = 10

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
# saved separately as RAW_JSON_FILE.
PAPERS_JSONL_FILE = Path("work/ieee_papers.jsonl")
RAW_JSON_FILE = Path("work/ieee_raw.json")


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


def main() -> None:
    if not IEEE_API_KEY:
        raise RuntimeError("Please set IEEE_API_KEY in .env first.")

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
        raise RuntimeError("At least one IEEE search parameter must be configured.")

    if not 1 <= max_records <= 200:
        raise RuntimeError("max_records must be between 1 and 200.")

    if start_record < 1:
        raise RuntimeError("start_record must be >= 1.")

    if save_count < 0:
        raise RuntimeError("save_count must be >= 0.")

    if request_interval_seconds < 0:
        raise RuntimeError("request_interval_seconds must be >= 0.")

    if not isinstance(http_403_retry_count, int) or http_403_retry_count < 0:
        raise RuntimeError("http_403_retry_count must be an integer >= 0.")

    if http_403_retry_wait_seconds < 0:
        raise RuntimeError("http_403_retry_wait_seconds must be >= 0.")

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

    print()
    print(f"Finished.")
    print(f"Pages requested: {pages_requested}")
    print(f"Papers saved to JSONL: {saved}")
    print(f"  with DOI: {with_doi}, without DOI: {without_doi}")
    print(f"JSONL: {PAPERS_JSONL_FILE}")
    print(f"Raw JSON: {RAW_JSON_FILE}")


if __name__ == "__main__":
    main()
