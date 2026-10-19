#!/usr/bin/env python3
"""
Scraper & Chapter Stitcher for 《飞升从考试开始》 (The Path to Ascension Begins with the Exam)
Source: https://www.52shuku.net/jiakong/05_b/bkeSI.html

How 52shuku pagination works & why this script stitches raw HTML before parsing:
- 52shuku splits the novel into 568 fixed-byte HTML pages (bkeSI_2.html .. bkeSI_569.html),
  cutting off raw HTML right in the middle of <p>...</p> tags across page boundaries.
- Parsing each page's DOM independently would prematurely close split paragraphs.
- This script:
  1. Downloads and caches all 568 pages with bounded concurrency (4 workers) and a thread-safe
     rate limiter (~4 requests/sec max) to avoid WAF/IP blocks.
  2. Enforces a strict 5 MB cap on both raw HTTP reads and gzip decompression to prevent memory DoS.
  3. Extracts the exact body slice between `ad_header.js` and `<div class="pagination2">` on each page.
  4. Concatenates all 568 raw HTML slices into a single continuous stream so every split `<p>...</p>`
     tag reunites seamlessly across page boundaries.
  5. Unescapes HTML entities and strips all HTML tags/angle brackets to prevent HTML/XSS injection
     in Markdown previews, and removes site watermarks.
  6. Splits the clean paragraph stream by chapter headings (`第X章 ...` / `番外`) into `raw_chapters/`
     with duplicate-filename collision protection so no chapter is ever silently overwritten.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import html
import io
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

INDEX_URL = "https://www.52shuku.net/jiakong/05_b/bkeSI.html"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Security & Operational Limits
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB hard cap on HTTP body & gzip stream
MAX_WORKERS = 4                       # Polite concurrency limit
MIN_REQUEST_INTERVAL_SEC = 0.25       # Max ~4 network requests/second across all threads

_rate_limit_lock = threading.Lock()
_last_request_ts = 0.0

ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = ROOT_DIR / ".cache" / "pages"
RAW_CHAPTERS_DIR = ROOT_DIR / "raw_chapters"

# Regex patterns for 52shuku extraction and cleaning
PAGE_LINK_RE = re.compile(
    r'<li class="mulu"><a href="(https://www\.52shuku\.net/jiakong/05_b/bkeSI_\d+\.html)">([^<]+)</a></li>'
)
BODY_SLICE_RE = re.compile(
    r'ad_header\.js"></script>(.*?)<div class="pagination2">',
    re.DOTALL | re.IGNORECASE,
)
INLINE_TAG_RE = re.compile(r"</?(?:a|span|strong|em|b|i|font)\b[^>]*>", re.IGNORECASE)
ANY_HTML_TAG_RE = re.compile(r"<[^>]+>")
PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)

# Chapter header detection: matches "第1章 ...", "第123章 ...", or "番外..."
CHAPTER_HEADER_RE = re.compile(
    r"^第\s*(\d+)\s*章(?:\s+(.*))?$"
)
EXTRA_HEADER_RE = re.compile(
    r"^番外(?:\s*[:：·\d一二三四五六七八九十]+\s*)?(.*)$"
)
AFTERWORD_HEADER_RE = re.compile(
    r"^作者有话说[:：]?$"
)

WATERMARK_PATTERNS = (
    "小伙伴们如果觉得52书库不错",
    "如果觉得52不错，记得收藏唯一网址",
    "小贴士:找看好看得小说，就来52书库",
    "传送门：排行榜单",
)


class ChapterData(NamedTuple):
    index: int
    chapter_num: int | None
    title_zh: str
    filename: str
    paragraphs: list[str]
    char_count: int


def _throttle_network_request() -> None:
    """Enforce a minimum interval between outgoing HTTP requests across all worker threads."""
    global _last_request_ts
    with _rate_limit_lock:
        now = time.monotonic()
        elapsed = now - _last_request_ts
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            time.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)
        _last_request_ts = time.monotonic()


def _read_bounded(stream: io.BufferedReader | gzip.GzipFile, max_bytes: int) -> bytes:
    """Read up to max_bytes from a stream; raise ValueError if response exceeds limit."""
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Response exceeded maximum allowed size of {max_bytes:,} bytes")
    return data


def fetch_url(url: str, retries: int = 4, timeout: float = 20.0) -> str:
    """Fetch URL content with bounded memory reads, rate limiting, and exponential backoff."""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            _throttle_network_request()
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Referer": INDEX_URL,
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = _read_bounded(resp, MAX_RESPONSE_BYTES)
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes)) as gz:
                        raw_bytes = _read_bounded(gz, MAX_RESPONSE_BYTES)
                return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            last_err = exc
            sleep_sec = 1.2 * (2 ** (attempt - 1))
            time.sleep(sleep_sec)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_err}")


def get_page_urls(index_html: str) -> list[tuple[int, str, str]]:
    """Extract ordered (page_number, url, label) tuples from the index page."""
    matches = PAGE_LINK_RE.findall(index_html)
    pages: list[tuple[int, str, str]] = []
    seen_urls: set[str] = set()
    for idx, (url, label) in enumerate(matches, start=1):
        if url not in seen_urls:
            seen_urls.add(url)
            pages.append((idx, url, label.strip()))
    return pages


def download_page(page_info: tuple[int, str, str]) -> tuple[int, str]:
    """Download a single page (using disk cache if already downloaded)."""
    page_num, url, _ = page_info
    cache_file = CACHE_DIR / f"page_{page_num:04d}.html"
    if cache_file.exists() and cache_file.stat().st_size > 500:
        return page_num, cache_file.read_text(encoding="utf-8")

    content = fetch_url(url)
    cache_file.write_text(content, encoding="utf-8")
    return page_num, content


def extract_body_slice(page_html: str, page_num: int) -> str:
    """Extract the article content slice between ad_header.js and pagination2."""
    match = BODY_SLICE_RE.search(page_html)
    if not match:
        raise ValueError(f"Could not locate content boundaries on page {page_num}")
    return match.group(1)


def clean_paragraph_text(raw_p: str) -> str:
    """
    Strip inline HTML tags, unescape HTML entities, strip any newly decoded tags,
    and escape stray angle brackets to prevent HTML/XSS injection in Markdown viewers.
    """
    # 1. Strip raw HTML tags from the source fragment
    no_tags = INLINE_TAG_RE.sub("", raw_p)
    no_tags = ANY_HTML_TAG_RE.sub("", no_tags)
    # 2. Unescape HTML entities (e.g. &quot;, &amp;, or encoded tags like &lt;script&gt;)
    unescaped = html.unescape(no_tags)
    # 3. Strip any tags that emerged after unescaping & neutralize stray angle brackets
    sanitized = ANY_HTML_TAG_RE.sub("", unescaped)
    sanitized = sanitized.replace("<", "＜").replace(">", "＞")
    # 4. Strip full-width ideographic spaces (\u3000) and surrounding whitespace
    return sanitized.strip(" \t\r\n\u3000\xa0")


def is_watermark(text: str) -> bool:
    """Return True if the paragraph is a 52shuku promotional watermark."""
    if not text:
        return True
    for pat in WATERMARK_PATTERNS:
        if pat in text:
            return True
    return False


def parse_paragraphs_from_stream(stitched_html: str) -> list[str]:
    """Extract clean paragraphs from the stitched raw HTML stream."""
    raw_paragraphs = PARAGRAPH_RE.findall(stitched_html)
    paragraphs: list[str] = []
    for raw_p in raw_paragraphs:
        cleaned = clean_paragraph_text(raw_p)
        if not is_watermark(cleaned):
            paragraphs.append(cleaned)
    return paragraphs


def split_into_chapters(paragraphs: list[str]) -> list[ChapterData]:
    """
    Split unified paragraph list into Synopsis (000), numbered chapters, Extras, and Afterword.
    Guarantees unique filenames even if the source text contains duplicate chapter numbers.
    """
    chapters: list[ChapterData] = []
    current_chapter_num: int | None = 0
    current_title = "文案与简介 (Synopsis & Introduction)"
    current_paras: list[str] = []
    extra_counter = 0
    used_filenames: dict[str, int] = {}

    def allocate_unique_filename(base_stem: str) -> str:
        count = used_filenames.get(base_stem, 0) + 1
        used_filenames[base_stem] = count
        if count == 1:
            return f"{base_stem}.md"
        return f"{base_stem}_part{count}.md"

    def flush_current() -> None:
        nonlocal current_paras
        if not current_paras:
            return
        idx = len(chapters)
        if current_chapter_num == 0:
            base_stem = "000_Synopsis"
        elif current_chapter_num is None:
            base_stem = "999_Afterword"
        elif current_chapter_num < 0:
            base_stem = f"900_Extra_{abs(current_chapter_num):02d}"
        else:
            base_stem = f"{current_chapter_num:03d}_Chapter_{current_chapter_num:03d}"

        filename = allocate_unique_filename(base_stem)
        char_count = sum(len(p) for p in current_paras)
        chapters.append(
            ChapterData(
                index=idx,
                chapter_num=current_chapter_num,
                title_zh=current_title,
                filename=filename,
                paragraphs=list(current_paras),
                char_count=char_count,
            )
        )
        current_paras = []

    for para in paragraphs:
        m_ch = CHAPTER_HEADER_RE.match(para)
        m_extra = EXTRA_HEADER_RE.match(para) if not m_ch else None
        if m_ch:
            flush_current()
            ch_num = int(m_ch.group(1))
            ch_subtitle = (m_ch.group(2) or "").strip()
            current_chapter_num = ch_num
            current_title = f"第{ch_num}章 {ch_subtitle}".strip()
        elif m_extra and len(para) <= 40:
            flush_current()
            extra_counter += 1
            current_chapter_num = -extra_counter
            current_title = para.strip()
        elif AFTERWORD_HEADER_RE.match(para) and current_chapter_num and current_chapter_num > 100:
            flush_current()
            current_chapter_num = None
            current_title = "作者有话说 (Author's Afterword)"
        else:
            current_paras.append(para)

    flush_current()
    return chapters


def write_chapters(chapters: list[ChapterData]) -> None:
    """Write individual Markdown files for each chapter and an INDEX.md manifest."""
    RAW_CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)

    for ch in chapters:
        out_path = RAW_CHAPTERS_DIR / ch.filename
        body = "\n\n".join(ch.paragraphs)
        md_content = f"# {ch.title_zh}\n\n{body}\n"
        out_path.write_text(md_content, encoding="utf-8")

    # Write INDEX.md
    total_chars = sum(c.char_count for c in chapters)
    lines = [
        "# 《飞升从考试开始》 — Raw Chapters Index",
        "",
        f"- **Source**: `{INDEX_URL}`",
        f"- **Total Files**: {len(chapters)} (Synopsis + {len(chapters) - 1} Chapters)",
        f"- **Total Chinese Characters**: {total_chars:,}",
        "",
        "| # | File | Chinese Title | Characters | Paragraphs |",
        "|---:|:-----|:--------------|-----------:|-----------:|",
    ]
    for ch in chapters:
        if ch.chapter_num == 0:
            num_label = "000"
        elif ch.chapter_num is None:
            num_label = "END"
        elif ch.chapter_num < 0:
            num_label = f"EX{abs(ch.chapter_num):02d}"
        else:
            num_label = f"{ch.chapter_num:03d}"
        lines.append(
            f"| {num_label} | [{ch.filename}]({ch.filename}) | {ch.title_zh} | {ch.char_count:,} | {len(ch.paragraphs)} |"
        )

    (RAW_CHAPTERS_DIR / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] Fetching table of contents: {INDEX_URL}")
    index_cache = CACHE_DIR / "index.html"
    if index_cache.exists() and index_cache.stat().st_size > 1000:
        index_html = index_cache.read_text(encoding="utf-8")
    else:
        index_html = fetch_url(INDEX_URL)
        index_cache.write_text(index_html, encoding="utf-8")

    pages = get_page_urls(index_html)
    print(f"      Found {len(pages)} content pages to fetch.")

    print(
        f"[2/4] Downloading & caching {len(pages)} pages "
        f"({MAX_WORKERS} workers, rate-limited to ~{1.0 / MIN_REQUEST_INTERVAL_SEC:.0f} req/s)..."
    )
    t0 = time.perf_counter()
    page_html_map: dict[int, str] = {}
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_page = {pool.submit(download_page, p): p for p in pages}
        for future in concurrent.futures.as_completed(future_to_page):
            p_num, p_html = future.result()
            page_html_map[p_num] = p_html
            completed += 1
            if completed % 50 == 0 or completed == len(pages):
                elapsed = time.perf_counter() - t0
                print(f"      Progress: {completed}/{len(pages)} pages ({elapsed:.1f}s)")

    print("[3/4] Stitching raw HTML across all page boundaries & cleaning tags...")
    ordered_slices: list[str] = []
    for p_num in range(1, len(pages) + 1):
        slice_html = extract_body_slice(page_html_map[p_num], p_num)
        ordered_slices.append(slice_html)

    stitched_html = "".join(ordered_slices)
    paragraphs = parse_paragraphs_from_stream(stitched_html)
    print(f"      Extracted {len(paragraphs):,} clean paragraphs.")

    print("[4/4] Splitting into indexed chapters and writing to raw_chapters/...")
    chapters = split_into_chapters(paragraphs)
    write_chapters(chapters)

    total_chars = sum(c.char_count for c in chapters)
    print(
        f"Done! Wrote {len(chapters)} files ({total_chars:,} Chinese characters) to {RAW_CHAPTERS_DIR}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
