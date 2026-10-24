#!/usr/bin/env python3
"""
Markdown-to-HTML Local Reader Builder & Progress Server for 《飞升从考试开始》
(The Path to Ascension Begins with the Exam)

Features:
1. Parses `GLOSSARY.md` and `translated_chapters/*.md` into `site/index.html` + `site/chapters_data.js`.
2. Generates a custom 64x64 Windows icon (`reader_icon.ico` and `site/favicon.ico`) in pure Python.
3. Maintains a persistent JSON database (`reading_progress.json`) tracking:
   - `last_read_code`: The last chapter you were reading
   - `read_chapters`: All chapters you have already read
   - `scroll_positions`: Per-chapter vertical scroll position
   - `settings`: Theme, font size/family, monologue style, and tooltip modes
4. When launched via `Start_Reader.bat` (`py build_reader.py --serve`), runs a local server on
   127.0.0.1 that automatically syncs all reading progress back to `reading_progress.json` on disk
   and supports one-click live rebuilding (`POST /api/rebuild`) when new chapters are translated.
"""

from __future__ import annotations

import argparse
import datetime
import html
import http.server
import json
import re
import socket
import socketserver
import struct
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
RAW_CHAPTERS_DIR = ROOT_DIR / "raw_chapters"
TRANSLATED_CHAPTERS_DIR = ROOT_DIR / "translated_chapters"
GLOSSARY_FILE = ROOT_DIR / "GLOSSARY.md"
PROGRESS_DB_FILE = ROOT_DIR / "reading_progress.json"
ICON_FILE = ROOT_DIR / "reader_icon.ico"
SITE_DIR = ROOT_DIR / "site"

MAX_API_BODY_BYTES = 64 * 1024  # 64 KB cap on local POST bodies
_db_lock = threading.Lock()

# Regex patterns for custom Markdown syntax
YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)
MD_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$")
NAV_LINE_RE = re.compile(r"^\[(?:←|Table of Contents|Next Chapter).*\|.*$", re.IGNORECASE)

# Unattributed dialogue line wrapped in tooltip: [—"Speech."](# "Speaker: ...")
# Robust to: (# "tip"), (<# "tip">), (<#> "tip"), escaped quotes, single quotes, and curly quotes
UNATTRIBUTED_DIALOGUE_RE = re.compile(
    r'^\s*\[([—\-]+["“].*?["”])\]\(\s*<?#\S*?>?\s*\\?["“](.*?)\\?["”]\s*>?\s*\)\s*$|'
    r'^\s*\[([—\-]+["“].*?["”])\]\(\s*<?#\S*?>?\s*\\?[\'‘](.*?)\\?[\'’]\s*>?\s*\)\s*$'
)

# Inline tooltip link: [text](# "tooltip")
# Robust to: (# "tip"), (<# "tip">), (<#> "tip"), escaped quotes, single quotes, and curly quotes
INLINE_TOOLTIP_RE = re.compile(
    r'\[([^\[\]\r\n]+?)\]\(\s*<?#\S*?>?\s*\\?["“](.*?)\\?["”]\s*>?\s*\)|'
    r'\[([^\[\]\r\n]+?)\]\(\s*<?#\S*?>?\s*\\?[\'‘](.*?)\\?[\'’]\s*>?\s*\)'
)

# Monologue brackets: [Monologue text]
# Guarded against:
# - Preceding * or ! (e.g. **[Spirit Stones]**, ![image](...))
# - Following (, [, or * (e.g. markdown links [anchor](url), reference links [anchor][ref], or **[system]**)
MONOLOGUE_RE = re.compile(r"(?<![\*!])\[([^\[\]\r\n]+)\](?!\s*[\(\[\*])")

# General markdown link: [text](url) or [text](url "title")
GENERAL_LINK_RE = re.compile(
    r'\[([^\[\]\r\n]+?)\]\(\s*<?([^\s\)>]+)>?(?:\s+\\?["“\'](.*?)\\?["”\'])?\s*\)'
)

# Attributed dialogue segments inside a line starting with —" or —“
DIALOGUE_SEGMENT_RE = re.compile(
    r'([—\-]+)(["“].*?["”])(?:\s*-\s*([^—]+?))?(?=(?:[—\-]+["“])|$)'
)


def generate_ico_file(target_path: Path) -> None:
    """
    Generate a 64x64 32-bit BGRA Windows .ico file depicting an Immortal Jade & Gold Scroll/Book.
    Uses pure Python `struct` (zero external dependencies).
    """
    w, h = 64, 64
    pixels = bytearray(w * h * 4)

    def set_px(x: int, y: int, r: int, g: int, b: int, a: int = 255) -> None:
        if 0 <= x < w and 0 <= y < h:
            # BMP inside ICO is stored bottom-up
            row = (h - 1 - y)
            idx = (row * w + x) * 4
            pixels[idx] = b
            pixels[idx + 1] = g
            pixels[idx + 2] = r
            pixels[idx + 3] = a

    # Draw rounded dark-jade badge background with gold border and scroll pages
    cx, cy = 31.5, 31.5
    for y in range(h):
        for x in range(w):
            dx = abs(x - cx)
            dy = abs(y - cy)
            # Rounded square mask
            corner_dist = max(dx - 20, 0) ** 2 + max(dy - 20, 0) ** 2
            if dx <= 28 and dy <= 28 and corner_dist <= 64:
                # Gold outer trim
                if dx >= 25 or dy >= 25 or corner_dist >= 36:
                    set_px(x, y, 217, 164, 65, 255)
                else:
                    # Deep immortal jade background
                    set_px(x, y, 16, 42, 35, 255)

    # Draw open scroll / book parchment in center
    for y in range(14, 50):
        for x in range(15, 49):
            # Gold scroll rods at top and bottom
            if y in (14, 15, 48, 49):
                set_px(x, y, 235, 185, 82, 255)
            elif 17 <= x <= 46:
                # Warm sepia parchment
                set_px(x, y, 248, 238, 214, 255)

    # Draw crimson-gold spine / lightning accent and ink text lines on the scroll
    for y in range(21, 44, 5):
        for x in range(21, 43):
            if y == 21 and 26 <= x <= 37:
                set_px(x, y, 178, 58, 42, 255)
                set_px(x, y + 1, 178, 58, 42, 255)
            elif y > 21:
                set_px(x, y, 48, 62, 55, 255)
                set_px(x, y + 1, 48, 62, 55, 255)

    # AND mask (1 bit per pixel, padded to 32-bit boundary per row -> 8 bytes * 64 rows = 512 bytes)
    and_mask = bytes(512)

    # BITMAPINFOHEADER (40 bytes, height is doubled for XOR + AND masks)
    bih = struct.pack(
        "<IIIHHIIIIII",
        40,                 # biSize
        w,                  # biWidth
        h * 2,              # biHeight (XOR + AND)
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression (BI_RGB)
        len(pixels) + len(and_mask),
        0,
        0,
        0,
        0,
    )

    image_data = bih + bytes(pixels) + and_mask
    # ICONDIR (6 bytes) + ICONDIRENTRY (16 bytes)
    icondir = struct.pack("<HHH", 0, 1, 1)
    icondirentry = struct.pack(
        "<BBBBHHII",
        w,                  # bWidth
        h,                  # bHeight
        0,                  # bColorCount
        0,                  # bReserved
        1,                  # wPlanes
        32,                 # wBitCount
        len(image_data),    # dwBytesInRes
        6 + 16,             # dwImageOffset
    )

    target_path.write_bytes(icondir + icondirentry + image_data)


def load_progress_db() -> dict[str, Any]:
    """Load `reading_progress.json` or initialize defaults if missing/corrupt."""
    default_db: dict[str, Any] = {
        "last_read_code": "000",
        "last_read_title": "Synopsis & Introduction (文案与简介)",
        "last_read_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "read_chapters": ["000"],
        "scroll_positions": {},
        "settings": {
            "theme": "midnight",
            "fontSize": "18",
            "fontFamily": "sans",
            "monologueStyle": "pill",
            "speakerMode": "hidden",
            "slangMode": "hidden",
            "scrollMode": "paginated",
        },
    }
    if not PROGRESS_DB_FILE.exists():
        save_progress_db(default_db)
        return default_db

    try:
        data = json.loads(PROGRESS_DB_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_db
        for k, v in default_db.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return default_db


def save_progress_db(data: dict[str, Any]) -> dict[str, Any]:
    """Thread-safely persist reading progress to `reading_progress.json`."""
    with _db_lock:
        current = {}
        if PROGRESS_DB_FILE.exists():
            try:
                current = json.loads(PROGRESS_DB_FILE.read_text(encoding="utf-8"))
            except Exception:
                current = {}

        if isinstance(data.get("last_read_code"), str):
            current["last_read_code"] = data["last_read_code"]
        if isinstance(data.get("last_read_title"), str):
            current["last_read_title"] = data["last_read_title"]
        current["last_read_at"] = datetime.datetime.now().isoformat(timespec="seconds")

        if isinstance(data.get("read_chapters"), list):
            # Deduplicate while preserving sorted order
            clean_codes = sorted({str(c) for c in data["read_chapters"] if str(c).strip()})
            current["read_chapters"] = clean_codes

        if isinstance(data.get("scroll_positions"), dict):
            scrolls = current.get("scroll_positions", {})
            if not isinstance(scrolls, dict):
                scrolls = {}
            for k, v in data["scroll_positions"].items():
                if isinstance(v, (int, float)):
                    scrolls[str(k)] = int(v)
            current["scroll_positions"] = scrolls

        if isinstance(data.get("settings"), dict):
            settings = current.get("settings", {})
            if not isinstance(settings, dict):
                settings = {}
            for k, v in data["settings"].items():
                if isinstance(v, str):
                    settings[str(k)] = v
            current["settings"] = settings

        PROGRESS_DB_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return current


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "entry"


def parse_glossary(glossary_path: Path) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """
    Parse GLOSSARY.md into:
    1. A list of glossary card dicts for the modal.
    2. A list of `(display_term, glossary_id)` pairs sorted longest-first for automatic linking.
    """
    if not glossary_path.exists():
        return [], []

    content = glossary_path.read_text(encoding="utf-8")
    entries: list[dict[str, str]] = []
    auto_links: dict[str, str] = {}

    for block in YAML_BLOCK_RE.findall(content):
        fields: dict[str, str] = {}
        current_key: str | None = None
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if raw_line.startswith("  - ") and current_key:
                fields[current_key] = fields.get(current_key, "") + " " + line[2:].strip()
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                current_key = k.strip()
                fields[current_key] = v.strip()

        if "Character Name" in fields:
            default_name = fields.get("Character Name", "")
            override_name = fields.get("English Name (optional)", "")
            display_name = override_name if override_name else default_name
            zh_name = fields.get("Chinese Name", "")
            sex = fields.get("Sex", "Character")
            titles = fields.get("Titles / Epithets", "")
            role = fields.get("Sect & Role", "")
            desc = fields.get("Speech Style & Personality", "") or fields.get("Relationships", "")
            entry_id = f"char-{slugify(display_name)}"

            entries.append(
                {
                    "id": entry_id,
                    "category": "Characters",
                    "name": display_name,
                    "zh": zh_name,
                    "sex": sex,
                    "titles": titles,
                    "role": role,
                    "desc": desc,
                }
            )
            if display_name:
                auto_links[display_name] = entry_id
                auto_links[f"{display_name}'s"] = entry_id
                parts = display_name.split()
                if len(parts) == 2 and len(parts[1]) >= 4:
                    auto_links[parts[1]] = entry_id

            for raw_t in re.split(r"[,;]", titles):
                clean_t = re.sub(r"\([^)]*\)", "", raw_t).strip()
                if clean_t and len(clean_t) >= 6 and clean_t.lower() not in {"master", "grandmaster"}:
                    auto_links[clean_t] = entry_id

        elif "Location Name" in fields:
            default_loc = fields.get("Location Name", "")
            override_loc = fields.get("Location English Name (optional)", "")
            display_loc = override_loc if override_loc else default_loc
            zh_loc = fields.get("Location Chinese Name", "")
            loc_type = fields.get("Type", "Location")
            notes = fields.get("Notes", "")
            entry_id = f"loc-{slugify(display_loc)}"

            entries.append(
                {
                    "id": entry_id,
                    "category": "Locations",
                    "name": display_loc,
                    "zh": zh_loc,
                    "sex": loc_type,
                    "titles": loc_type,
                    "role": "Cultivation World",
                    "desc": notes,
                }
            )
            if display_loc and len(display_loc) >= 5:
                auto_links[display_loc] = entry_id

    in_section_3 = False
    current_subcat = "Realms & Terms"
    for line in content.splitlines():
        if line.startswith("## 3."):
            in_section_3 = True
            continue
        elif line.startswith("## 4."):
            in_section_3 = False
            break

        if in_section_3:
            if line.startswith("### "):
                current_subcat = line[4:].split("(")[0].strip()
                continue
            m = MD_TABLE_ROW_RE.match(line.strip())
            if m:
                col1, col2, col3 = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                if col1.startswith("---") or col1 in {"Chinese Term", "Chinese Name"}:
                    continue
                clean_en = col2.strip("*").strip()
                entry_id = f"term-{slugify(clean_en)}"
                entries.append(
                    {
                        "id": entry_id,
                        "category": "Realms",
                        "name": clean_en,
                        "zh": col1,
                        "sex": current_subcat,
                        "titles": current_subcat,
                        "role": "Cultivation Term",
                        "desc": col3,
                    }
                )

    sorted_links = sorted(auto_links.items(), key=lambda kv: len(kv[0]), reverse=True)
    return entries, sorted_links


class InlineProcessor:
    """
    Safely converts custom inline Markdown syntax (tooltips, monologues, bold/italics,
    and character glossary links) into HTML without colliding tags or allowing raw HTML injection.
    """

    def __init__(self, glossary_entries: list[dict[str, str]], auto_links: list[tuple[str, str]]) -> None:
        self.entry_map = {e["id"]: e for e in glossary_entries}
        self.auto_links = auto_links
        if auto_links:
            escaped_terms = [re.escape(term) for term, _ in auto_links]
            self.char_regex: re.Pattern[str] | None = re.compile(
                r"\b(" + "|".join(escaped_terms) + r")\b"
            )
            self.term_to_id = dict(auto_links)
        else:
            self.char_regex = None
            self.term_to_id = {}

    def process_inline(self, text: str, allow_monologue: bool = True) -> str:
        placeholders: list[str] = []

        def big_placeholder(html_snippet: str) -> str:
            idx = len(placeholders)
            placeholders.append(html_snippet)
            return f"\x00PH{idx}PH\x00"

        def replace_tooltip(match: re.Match[str]) -> str:
            inner_raw = match.group(1) if match.group(1) is not None else match.group(3)
            tip_raw = match.group(2) if match.group(2) is not None else match.group(4)
            tip_raw = (tip_raw or "").replace('\\"', '"').replace("\\'", "'")
            inner_escaped = html.escape(inner_raw)
            tip_escaped = html.escape(tip_raw, quote=True)

            badge = "Cultural Note / Idiom"
            if tip_raw.lower().startswith("speaker:"):
                badge = "🗣 Speaker Tooltip"
                cls = "has-tooltip unattributed-dialogue"
            else:
                cls = "has-tooltip cultural-note"

            snippet = (
                f'<span class="{cls}" data-tip-type="{html.escape(badge, quote=True)}" '
                f'data-tip-text="{tip_escaped}">{inner_escaped}</span>'
            )
            return big_placeholder(snippet)

        work = INLINE_TOOLTIP_RE.sub(replace_tooltip, text)

        def replace_link(match: re.Match[str]) -> str:
            label_raw = match.group(1)
            url_raw = match.group(2).strip()
            title_raw = (match.group(3) or "").replace('\\"', '"').replace("\\'", "'")
            label_processed = self._process_text_primitives(label_raw, placeholders)
            url_escaped = html.escape(url_raw, quote=True)
            title_attr = f' title="{html.escape(title_raw, quote=True)}"' if title_raw else ""
            target_attr = ' target="_blank" rel="noopener noreferrer"' if url_raw.startswith(("http://", "https://")) else ""
            snippet = f'<a href="{url_escaped}" class="novel-link"{title_attr}{target_attr}>{label_processed}</a>'
            return big_placeholder(snippet)

        work = GENERAL_LINK_RE.sub(replace_link, work)

        if allow_monologue:
            def replace_monologue(match: re.Match[str]) -> str:
                mono_inner = match.group(1).strip()
                processed_inner = self._process_text_primitives(mono_inner, placeholders)
                snippet = f'<span class="monologue-span">{processed_inner}</span>'
                return big_placeholder(snippet)

            work = MONOLOGUE_RE.sub(replace_monologue, work)

        work = self._process_text_primitives(work, placeholders)

        for _ in range(4):
            if "\x00PH" not in work:
                break
            work = re.sub(
                r"\x00PH(\d+)PH\x00",
                lambda m: placeholders[int(m.group(1))],
                work,
            )
        return work

    def _process_text_primitives(self, raw_segment: str, placeholders: list[str]) -> str:
        def save_ph(snippet: str) -> str:
            idx = len(placeholders)
            placeholders.append(snippet)
            return f"\x00PH{idx}PH\x00"

        escaped = html.escape(raw_segment, quote=False)

        if self.char_regex is not None:
            def replace_char(m: re.Match[str]) -> str:
                term = m.group(1)
                gid = self.term_to_id.get(term)
                if not gid:
                    return term
                entry = self.entry_map.get(gid)
                if entry:
                    tip = f"{entry['name']} ({entry['zh']}) • {entry['sex']} — {entry['titles']}. Click to open Glossary card."
                else:
                    tip = "Click to view in Glossary"
                snippet = (
                    f'<span onclick="openGlossary(\'{gid}\')" '
                    f'class="char-ref has-char-tooltip" '
                    f'data-tip-type="👤 Glossary • Click to Open" '
                    f'data-tip-text="{html.escape(tip, quote=True)}">{term}</span>'
                )
                return save_ph(snippet)

            escaped = self.char_regex.sub(replace_char, escaped)

        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
        return escaped


def extract_clean_title(raw_title: str) -> str:
    """
    Strips markdown tooltips, links, formatting, and trailing quotes from chapter headings.
    Handles nested parentheses cleanly:
    e.g. '# Chapter 180: [Not Swayed by External Gains](# "Literal CN: 不以物喜 (bù yǐ wù xǐ)...")'
    -> 'Chapter 180: Not Swayed by External Gains'
    """
    clean = raw_title.strip()
    # Strip markdown link [Anchor](Tooltip/URL with possible nested parens)
    clean = re.sub(r'\[([^\[\]]+)\]\((?:[^()]*|\([^()]*\))*\)', r'\1', clean)
    # If any unclosed or deeper nested brackets remain
    if "[" in clean and "](" in clean:
        clean = re.sub(r'\[([^\[\]]+)\]\(.*?\)', r'\1', clean)
    # Strip markdown emphasis or backticks
    clean = re.sub(r'[`*_]', '', clean)
    # Strip accidental unclosed quotes or parens at end
    clean = clean.strip().rstrip('"\') ')
    return clean


def convert_markdown_chapter_to_html(
    md_text: str,
    processor: InlineProcessor,
) -> tuple[str, str, int]:
    """
    Convert a translated chapter Markdown string into structured HTML.
    Returns (chapter_title_en, body_html, word_count).
    """
    lines = md_text.splitlines()
    title_en = ""
    paragraphs_raw: list[str] = []
    current_para: list[str] = []
    in_blockquote = False
    blockquote_lines: list[str] = []

    def flush_para() -> None:
        nonlocal current_para
        if current_para:
            joined = " ".join(current_para).strip()
            if joined:
                paragraphs_raw.append(joined)
            current_para = []

    def flush_blockquote() -> None:
        nonlocal blockquote_lines, in_blockquote
        if blockquote_lines:
            paragraphs_raw.append("BLOCKQUOTE::" + "\n".join(blockquote_lines))
            blockquote_lines = []
        in_blockquote = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if NAV_LINE_RE.match(stripped):
            continue

        if stripped.startswith("# ") and not title_en:
            flush_para()
            flush_blockquote()
            raw_title = stripped[2:].strip()
            title_en = extract_clean_title(raw_title)
            continue

        if stripped.startswith(">"):
            flush_para()
            in_blockquote = True
            bq_content = stripped[1:].strip()
            blockquote_lines.append(bq_content)
            continue
        elif in_blockquote and not stripped:
            flush_blockquote()
            continue
        elif in_blockquote:
            flush_blockquote()

        if stripped in {"*", "***", "---", "* * *"}:
            flush_para()
            paragraphs_raw.append("HR::")
            continue

        if not stripped:
            flush_para()
        else:
            current_para.append(stripped)

    flush_para()
    flush_blockquote()

    html_blocks: list[str] = []
    plain_words = 0

    for block in paragraphs_raw:
        if block == "HR::":
            html_blocks.append(
                '<div class="my-8 flex items-center justify-center gap-3 opacity-40 text-sm">'
                '<span>✦</span><span>✦</span><span>✦</span></div>'
            )
            continue

        if block.startswith("BLOCKQUOTE::"):
            bq_raw = block[len("BLOCKQUOTE::"):]
            plain_words += len(bq_raw.split())
            bq_html_lines: list[str] = []
            in_list = False
            for bline in bq_raw.splitlines():
                if not bline:
                    continue
                if bline.startswith("- ") or bline.startswith("* "):
                    if not in_list:
                        bq_html_lines.append('<ul class="list-disc pl-5 space-y-1 text-sm my-2">')
                        in_list = True
                    bq_html_lines.append(
                        f"<li>{processor.process_inline(bline[2:].strip(), allow_monologue=False)}</li>"
                    )
                else:
                    if in_list:
                        bq_html_lines.append("</ul>")
                        in_list = False
                    bq_html_lines.append(
                        f'<p class="mb-2 last:mb-0">{processor.process_inline(bline, allow_monologue=False)}</p>'
                    )
            if in_list:
                bq_html_lines.append("</ul>")

            html_blocks.append(
                f'<blockquote class="exam-scroll">{"".join(bq_html_lines)}</blockquote>'
            )
            continue

        plain_words += len(block.split())

        m_unattr = UNATTRIBUTED_DIALOGUE_RE.match(block)
        if m_unattr:
            speech_part = m_unattr.group(1) if m_unattr.group(1) is not None else m_unattr.group(3)
            tip_raw = (m_unattr.group(2) if m_unattr.group(2) is not None else m_unattr.group(4) or "").replace('\\"', '"').replace("\\'", "'")
            dash_match = re.match(r"^([—\-]+)(.*)$", speech_part, re.DOTALL)
            if dash_match:
                dash_str, quote_str = dash_match.group(1), dash_match.group(2)
            else:
                dash_str, quote_str = "—", speech_part

            processed_quote = processor.process_inline(quote_str, allow_monologue=False)
            html_blocks.append(
                f'<p class="dialogue-line">'
                f'<span class="has-tooltip unattributed-dialogue" '
                f'data-tip-type="🗣 Speaker Tooltip" '
                f'data-tip-text="{html.escape(tip_raw, quote=True)}">'
                f'<span class="dialogue-dash">{html.escape(dash_str)}</span>'
                f'<span class="dialogue-speech">{processed_quote}</span>'
                f'</span></p>'
            )
            continue

        if block.startswith(('—"', '—“', '-"', '-“')):
            tip_stash: list[str] = []

            def mask_tip(m: re.Match[str]) -> str:
                idx = len(tip_stash)
                tip_stash.append(m.group(0))
                return f"\x00TIP{idx}TIP\x00"

            def unmask_tips(s: str) -> str:
                if "\x00TIP" not in s:
                    return s
                return re.sub(
                    r"\x00TIP(\d+)TIP\x00",
                    lambda m: tip_stash[int(m.group(1))],
                    s,
                )

            masked_block = INLINE_TOOLTIP_RE.sub(mask_tip, block)
            segments = list(DIALOGUE_SEGMENT_RE.finditer(masked_block))
            if segments:
                parts_html: list[str] = []
                for seg in segments:
                    dash_str = seg.group(1)
                    quote_str = unmask_tips(seg.group(2))
                    tag_str = unmask_tips((seg.group(3) or "").strip())

                    processed_quote = processor.process_inline(quote_str, allow_monologue=False)
                    parts_html.append(
                        f'<span class="dialogue-dash">{html.escape(dash_str)}</span>'
                        f'<span class="dialogue-speech">{processed_quote}</span>'
                    )
                    if tag_str:
                        processed_tag = processor.process_inline(tag_str, allow_monologue=True)
                        parts_html.append(
                            f'<span class="dialogue-tag-sep">-</span>'
                            f'<span class="dialogue-tag">{processed_tag}</span> '
                        )
                html_blocks.append(f'<p class="dialogue-line">{"".join(parts_html).strip()}</p>')
                continue

        processed_para = processor.process_inline(block, allow_monologue=True)
        html_blocks.append(f"<p>{processed_para}</p>")

    return title_en, "\n".join(html_blocks), plain_words


def parse_chapter_sort_key(path: Path) -> tuple[int, int, str]:
    prefix = path.stem.split("_", 1)[0]
    if prefix.isdigit():
        return (0, int(prefix), path.stem)
    return (1, 999999, path.stem)


def collect_all_chapters(
    raw_dir: Path,
    translated_dir: Path,
    processor: InlineProcessor,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_files = sorted(
        (p for p in raw_dir.glob("*.md") if p.name.upper() != "INDEX.MD"),
        key=parse_chapter_sort_key,
    )
    translated_map: dict[str, Path] = {}
    translated_num_map: dict[int, Path] = {}
    if translated_dir.exists():
        for tp in sorted(translated_dir.glob("*.md"), key=parse_chapter_sort_key):
            if tp.name.upper() == "INDEX.MD":
                continue
            prefix = tp.stem.split("_", 1)[0]
            translated_map[prefix] = tp
            if prefix.isdigit():
                translated_num_map[int(prefix)] = tp

    all_chapters_meta: list[dict[str, Any]] = []
    translated_chapters: list[dict[str, Any]] = []
    processed_translated_paths: set[Path] = set()

    for idx, rf in enumerate(raw_files):
        prefix = rf.stem.split("_", 1)[0]
        raw_text = rf.read_text(encoding="utf-8")
        raw_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
        title_zh = raw_lines[0].lstrip("#").strip() if raw_lines else rf.stem
        char_count = sum(len(ln) for ln in raw_lines[1:])

        # Match either exact string prefix or normalized integer
        tp = translated_map.get(prefix)
        if not tp and prefix.isdigit():
            tp = translated_num_map.get(int(prefix))

        if tp:
            processed_translated_paths.add(tp.resolve())
            md_text = tp.read_text(encoding="utf-8")
            title_en, body_html, word_count = convert_markdown_chapter_to_html(md_text, processor)
            if not title_en:
                title_en = f"Chapter {int(prefix) if prefix.isdigit() else prefix}"
            t_idx = len(translated_chapters)
            translated_chapters.append(
                {
                    "tIdx": t_idx,
                    "rawIdx": idx,
                    "code": prefix,
                    "filename": tp.name,
                    "titleEn": title_en,
                    "titleZh": title_zh,
                    "words": f"{word_count:,} words",
                    "html": body_html,
                }
            )
            all_chapters_meta.append(
                {
                    "idx": idx,
                    "code": prefix,
                    "titleZh": title_zh,
                    "titleEn": title_en,
                    "chars": char_count,
                    "words": word_count,
                    "status": "translated",
                    "tIdx": t_idx,
                }
            )
        else:
            default_en = (
                "Synopsis & Introduction"
                if prefix == "000"
                else "Author's Afterword"
                if prefix == "999"
                else f"Chapter {int(prefix)}"
                if prefix.isdigit()
                else rf.stem
            )
            all_chapters_meta.append(
                {
                    "idx": idx,
                    "code": prefix,
                    "titleZh": title_zh,
                    "titleEn": default_en,
                    "chars": char_count,
                    "words": 0,
                    "status": "raw",
                    "tIdx": None,
                }
            )

    # In case any translated chapter was not matched to a raw file, append it safely
    if translated_dir.exists():
        for tp in sorted(translated_dir.glob("*.md"), key=parse_chapter_sort_key):
            if tp.name.upper() == "INDEX.MD" or tp.resolve() in processed_translated_paths:
                continue
            prefix = tp.stem.split("_", 1)[0]
            md_text = tp.read_text(encoding="utf-8")
            title_en, body_html, word_count = convert_markdown_chapter_to_html(md_text, processor)
            if not title_en:
                title_en = f"Chapter {int(prefix) if prefix.isdigit() else prefix}"
            t_idx = len(translated_chapters)
            translated_chapters.append(
                {
                    "tIdx": t_idx,
                    "rawIdx": len(all_chapters_meta),
                    "code": prefix,
                    "filename": tp.name,
                    "titleEn": title_en,
                    "titleZh": title_en,
                    "words": f"{word_count:,} words",
                    "html": body_html,
                }
            )
            all_chapters_meta.append(
                {
                    "idx": len(all_chapters_meta),
                    "code": prefix,
                    "titleZh": title_en,
                    "titleEn": title_en,
                    "chars": 0,
                    "words": word_count,
                    "status": "translated",
                    "tIdx": t_idx,
                }
            )

    return all_chapters_meta, translated_chapters


READER_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>The Path to Ascension Begins with the Exam — Local Reader</title>
  <link rel="icon" type="image/x-icon" href="favicon.ico" />
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --background: #111318;
      --card: #1a1d24;
      --foreground: #ebedf2;
      --muted-foreground: #9ca3af;
      --border: #2b303c;
      --primary: #60a5fa;
      --primary-foreground: #0f172a;
    }

    body[data-reader-theme="sepia"] {
      --reader-bg: #f4ecd8;
      --reader-card: #fbf6e9;
      --reader-fg: #2c221e;
      --reader-muted: #6e5a4f;
      --reader-border: #dfd3b8;
      --reader-accent: #8c4a27;
      --reader-monologue-bg: rgba(140, 74, 39, 0.10);
      --reader-monologue-fg: #5c3219;
      --reader-slang-bg: rgba(180, 83, 9, 0.10);
      --reader-speaker-bg: rgba(14, 116, 144, 0.09);
      --reader-char-hover: rgba(140, 74, 39, 0.14);
      --reader-tooltip-bg: #2c221e;
      --reader-tooltip-fg: #fbf6e9;
    }
    body[data-reader-theme="midnight"] {
      --reader-bg: #0d1117;
      --reader-card: #161b22;
      --reader-fg: #e6edf3;
      --reader-muted: #8b949e;
      --reader-border: #30363d;
      --reader-accent: #58a6ff;
      --reader-monologue-bg: rgba(88, 166, 255, 0.12);
      --reader-monologue-fg: #a5d6ff;
      --reader-slang-bg: rgba(245, 158, 11, 0.13);
      --reader-speaker-bg: rgba(56, 189, 248, 0.11);
      --reader-char-hover: rgba(88, 166, 255, 0.18);
      --reader-tooltip-bg: #1f2937;
      --reader-tooltip-fg: #f9fafb;
    }
    body[data-reader-theme="jade"] {
      --reader-bg: #0f1915;
      --reader-card: #15241e;
      --reader-fg: #e2f1ea;
      --reader-muted: #84a597;
      --reader-border: #253d33;
      --reader-accent: #34d399;
      --reader-monologue-bg: rgba(52, 211, 153, 0.12);
      --reader-monologue-fg: #6ee7b7;
      --reader-slang-bg: rgba(251, 191, 36, 0.12);
      --reader-speaker-bg: rgba(45, 212, 191, 0.11);
      --reader-char-hover: rgba(52, 211, 153, 0.18);
      --reader-tooltip-bg: #064e3b;
      --reader-tooltip-fg: #ecfdf5;
    }
    body[data-reader-theme="light"] {
      --reader-bg: #f8fafc;
      --reader-card: #ffffff;
      --reader-fg: #0f172a;
      --reader-muted: #64748b;
      --reader-border: #e2e8f0;
      --reader-accent: #2563eb;
      --reader-monologue-bg: rgba(37, 99, 235, 0.09);
      --reader-monologue-fg: #1e40af;
      --reader-slang-bg: rgba(217, 119, 6, 0.10);
      --reader-speaker-bg: rgba(2, 132, 199, 0.09);
      --reader-char-hover: rgba(37, 99, 235, 0.12);
      --reader-tooltip-bg: #0f172a;
      --reader-tooltip-fg: #f8fafc;
    }

    .novel-prose p {
      margin-bottom: 1.15em;
      line-height: var(--reader-lh, 1.85);
      font-size: var(--reader-fs, 18px);
    }

    /* 1. Monologue Styling (No literal [] brackets, no underlines!) */
    .monologue-span {
      transition: all 0.15s ease;
    }
    body[data-monologue-style="pill"] .monologue-span {
      background: var(--reader-monologue-bg);
      color: var(--reader-monologue-fg);
      padding: 0.14em 0.5em;
      border-radius: 0.375rem;
      font-style: italic;
    }
    body[data-monologue-style="italic"] .monologue-span {
      color: var(--reader-monologue-fg);
      font-style: italic;
      opacity: 0.9;
    }

    /* 2. Dialogue Line Styling (Flush — no vertical bar in front!) */
    .dialogue-line {
      padding-left: 0;
      border-left: none;
    }
    .dialogue-dash {
      color: var(--reader-accent);
      font-weight: 700;
      margin-right: 1px;
    }
    .dialogue-speech {
      font-weight: 500;
    }
    .dialogue-tag-sep {
      color: var(--reader-accent);
      font-weight: 700;
      padding: 0 2px;
    }
    .dialogue-tag {
      color: var(--reader-muted);
    }

    /* 3. Unattributed Dialogue & Cultural Slang (Hidden by default — zero underlines, zero symbols!) */
    .unattributed-dialogue,
    .cultural-note {
      border-bottom: none;
      text-decoration: none;
      transition: all 0.15s ease;
    }

    body[data-slang-mode="pill"] .cultural-note {
      background: var(--reader-slang-bg);
      padding: 0.12em 0.42em;
      border-radius: 0.375rem;
      cursor: help;
    }
    body[data-slang-mode="hover"] .cultural-note {
      cursor: help;
    }
    body[data-slang-mode="hover"] .cultural-note:hover {
      background: var(--reader-slang-bg);
      border-radius: 0.25rem;
    }

    body[data-speaker-mode="pill"] .unattributed-dialogue {
      background: var(--reader-speaker-bg);
      padding: 0.12em 0.45em;
      border-radius: 0.375rem;
      cursor: help;
    }
    body[data-speaker-mode="hover"] .unattributed-dialogue {
      cursor: help;
    }
    body[data-speaker-mode="hover"] .unattributed-dialogue:hover {
      background: var(--reader-speaker-bg);
      border-radius: 0.25rem;
    }

    /* 4. Interactive Character Names (No underline; hover tint + click opens Glossary Card) */
    .char-ref {
      cursor: pointer;
      border-radius: 0.3rem;
      padding: 0.05em 0.2em;
      margin: 0 -0.1em;
      transition: background-color 0.15s ease, color 0.15s ease;
    }
    .char-ref:hover {
      background-color: var(--reader-char-hover);
      color: var(--reader-accent);
    }

    .novel-link {
      color: var(--reader-accent);
      text-decoration: underline;
      text-underline-offset: 3px;
      transition: opacity 0.15s ease;
    }
    .novel-link:hover {
      opacity: 0.8;
    }

    /* Exam Scroll Blockquote */
    .exam-scroll {
      background: color-mix(in srgb, var(--reader-card) 88%, var(--reader-accent) 12%);
      border: 1px solid color-mix(in srgb, var(--reader-accent) 35%, var(--reader-border));
      border-left: 4px solid var(--reader-accent);
      border-radius: 0.75rem;
      padding: 1.25rem 1.5rem;
      margin: 1.5rem 0;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
    }

    @keyframes pulse-ring {
      0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--reader-accent) 60%, transparent); }
      70% { box-shadow: 0 0 0 8px transparent; }
      100% { box-shadow: 0 0 0 0 transparent; }
    }
    .glossary-card-active {
      border-color: var(--reader-accent) !important;
      background-color: color-mix(in srgb, var(--reader-accent) 12%, var(--reader-bg)) !important;
      animation: pulse-ring 1.2s ease-out 2;
    }

    #floating-tooltip {
      pointer-events: none;
      transition: opacity 0.12s ease;
      z-index: 100;
    }
  </style>
</head>
<body
  data-reader-theme="midnight"
  data-monologue-style="pill"
  data-slang-mode="hidden"
  data-speaker-mode="hidden"
  style="background-color: var(--reader-bg); color: var(--reader-fg);"
  class="antialiased min-h-screen flex flex-col transition-colors duration-200"
>
  <!-- Top Reading Progress Bar -->
  <div class="fixed top-0 left-0 right-0 h-1 bg-[var(--reader-border)] z-50">
    <div id="reading-progress" class="h-full transition-all duration-150" style="width: 5%; background-color: var(--reader-accent);"></div>
  </div>

  <!-- Sticky Top Navigation Header -->
  <header
    style="background-color: color-mix(in srgb, var(--reader-card) 92%, transparent); border-color: var(--reader-border);"
    class="sticky top-0 z-40 backdrop-blur-md border-b px-4 py-2.5"
  >
    <div class="max-w-5xl mx-auto flex flex-wrap items-center justify-between gap-3">
      <!-- Left: Book Title & Chapter Overview Button -->
      <div class="flex items-center gap-2.5">
        <button
          onclick="toggleModal('index-modal')"
          style="border-color: var(--reader-border); background-color: var(--reader-bg);"
          class="flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium hover:opacity-80 transition"
        >
          <span>📑</span>
          <span id="nav-chapters-count">Chapters</span>
        </button>

        <div class="hidden sm:block pl-1">
          <div class="flex items-center gap-2 text-xs font-medium opacity-75">
            <span>《飞升从考试开始》</span>
            <span>•</span>
            <span id="header-read-counter" class="font-semibold" style="color: var(--reader-accent);">0 Read</span>
          </div>
          <div id="header-chapter-title" class="text-sm font-semibold truncate max-w-xs">
            Loading...
          </div>
        </div>
      </div>

      <!-- Center: Mode Toggle (Paginated vs Infinite Scroll) -->
      <div
        style="background-color: var(--reader-bg); border-color: var(--reader-border);"
        class="flex items-center p-0.5 rounded-lg border text-xs font-medium"
      >
        <button
          id="mode-paginated-btn"
          onclick="setScrollMode('paginated')"
          class="px-3 py-1.5 rounded-md transition font-semibold"
          style="background-color: var(--reader-card); color: var(--reader-fg); box-shadow: 0 1px 2px rgba(0,0,0,0.08);"
        >
          📖 Paginated
        </button>
        <button
          id="mode-infinite-btn"
          onclick="setScrollMode('infinite')"
          class="px-3 py-1.5 rounded-md transition opacity-70 hover:opacity-100"
        >
          ♾️ Infinite Scroll
        </button>
      </div>

      <!-- Right: Glossary Drawer, Rebuild & Settings -->
      <div class="flex items-center gap-2">
        <button
          onclick="openGlossary()"
          style="border-color: var(--reader-border); background-color: var(--reader-bg);"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium hover:opacity-80 transition"
        >
          <span>👥</span>
          <span id="nav-glossary-count">Glossary</span>
        </button>

        <button
          id="rebuild-btn"
          onclick="triggerLiveRebuild()"
          style="border-color: var(--reader-border); background-color: var(--reader-bg);"
          class="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border text-xs font-medium hover:opacity-80 transition"
          title="Re-parse GLOSSARY.md and translated_chapters/ without restarting"
        >
          <span>🔄</span>
          <span class="hidden md:inline">Sync Files</span>
        </button>

        <button
          onclick="toggleModal('settings-popover')"
          style="border-color: var(--reader-border); background-color: var(--reader-bg);"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium hover:opacity-80 transition"
        >
          <span>⚙️</span>
          <span>Settings</span>
        </button>
      </div>
    </div>
  </header>

  <!-- Settings & Tooltips Popover Panel -->
  <div
    id="settings-popover"
    style="background-color: var(--reader-card); border-color: var(--reader-border);"
    class="hidden fixed right-4 top-14 z-50 w-80 sm:w-96 rounded-xl border p-4 shadow-2xl max-h-[85vh] overflow-y-auto"
  >
    <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--reader-border);">
      <h3 class="font-semibold text-sm">Reader Settings &amp; Tooltip Controls</h3>
      <button onclick="toggleModal('settings-popover')" class="text-xs opacity-60 hover:opacity-100">✕ Close</button>
    </div>

    <!-- Theme Presets -->
    <div class="mb-4">
      <label class="block text-xs font-medium opacity-75 mb-1.5">Color Theme</label>
      <div class="grid grid-cols-2 gap-2 text-xs">
        <button onclick="setTheme('midnight')" class="px-2.5 py-2 rounded-lg border text-left font-medium" style="background:#0d1117; color:#e6edf3; border-color:#30363d;">
          🌙 Midnight Ink
        </button>
        <button onclick="setTheme('sepia')" class="px-2.5 py-2 rounded-lg border text-left font-medium" style="background:#f4ecd8; color:#2c221e; border-color:#dfd3b8;">
          📜 Parchment Sepia
        </button>
        <button onclick="setTheme('jade')" class="px-2.5 py-2 rounded-lg border text-left font-medium" style="background:#0f1915; color:#e2f1ea; border-color:#253d33;">
          🎋 Immortal Jade
        </button>
        <button onclick="setTheme('light')" class="px-2.5 py-2 rounded-lg border text-left font-medium" style="background:#f8fafc; color:#0f172a; border-color:#cbd5e1;">
          ☀️ Alabaster Light
        </button>
      </div>
    </div>

    <!-- Monologue Style (No brackets!) -->
    <div class="mb-4">
      <label class="block text-xs font-medium opacity-75 mb-1.5">Inner Monologue Style (No `[ ]` brackets)</label>
      <div class="grid grid-cols-2 gap-2 text-xs">
        <button id="mono-pill" onclick="setMonologueStyle('pill')" class="px-2.5 py-1.5 rounded-lg border font-semibold" style="border-color: var(--reader-accent);">
          Tinted Pill (Default)
        </button>
        <button id="mono-italic" onclick="setMonologueStyle('italic')" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
          Clean Italic Only
        </button>
      </div>
    </div>

    <!-- Unattributed Speaker Tooltip Mode -->
    <div class="mb-4">
      <label class="block text-xs font-medium opacity-75 mb-1.5">Speaker Tooltips (Untagged Dialogue)</label>
      <div class="grid grid-cols-3 gap-1.5 text-xs">
        <button id="spk-hidden" onclick="setSpeakerMode('hidden')" class="px-2 py-1.5 rounded-lg border font-semibold" style="border-color: var(--reader-accent);">
          Hidden (Off)
        </button>
        <button id="spk-hover" onclick="setSpeakerMode('hover')" class="px-2 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
          Hover Only
        </button>
        <button id="spk-pill" onclick="setSpeakerMode('pill')" class="px-2 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
          Tinted Pill
        </button>
      </div>
    </div>

    <!-- Cultural Idiom / Slang Tooltip Mode -->
    <div class="mb-4">
      <label class="block text-xs font-medium opacity-75 mb-1.5">Idiom &amp; Slang Tooltips</label>
      <div class="grid grid-cols-3 gap-1.5 text-xs">
        <button id="slang-hidden" onclick="setSlangMode('hidden')" class="px-2 py-1.5 rounded-lg border font-semibold" style="border-color: var(--reader-accent);">
          Hidden (Off)
        </button>
        <button id="slang-hover" onclick="setSlangMode('hover')" class="px-2 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
          Hover Only
        </button>
        <button id="slang-pill" onclick="setSlangMode('pill')" class="px-2 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
          Tinted Pill
        </button>
      </div>
    </div>

    <!-- Font Family & Size -->
    <div class="mb-3">
      <label class="block text-xs font-medium opacity-75 mb-1.5">Font Family</label>
      <div class="grid grid-cols-2 gap-2 text-xs">
        <button onclick="setFontFamily('serif')" class="px-2.5 py-1.5 rounded-lg border font-serif" style="border-color: var(--reader-border);">
          Book Serif
        </button>
        <button onclick="setFontFamily('sans')" class="px-2.5 py-1.5 rounded-lg border font-sans" style="border-color: var(--reader-border);">
          Clean Sans
        </button>
      </div>
    </div>

    <div>
      <div class="flex justify-between text-xs mb-1">
        <span class="opacity-75">Font Size</span>
        <span id="fs-label" class="font-semibold">18px</span>
      </div>
      <input
        id="fs-slider"
        type="range"
        min="14"
        max="24"
        value="18"
        oninput="setFontSize(this.value)"
        class="w-full"
      />
    </div>
  </div>

  <!-- Main Reader Container -->
  <main class="flex-1 px-4 py-8">
    <div class="max-w-3xl mx-auto">
      <!-- Top Chapter Jump Bar (Paginated Mode) -->
      <div id="top-pagination-bar" class="flex flex-wrap items-center justify-between gap-2 mb-6 text-xs">
        <button
          id="prev-btn-top"
          onclick="stepChapter(-1)"
          style="background-color: var(--reader-card); border-color: var(--reader-border);"
          class="px-3.5 py-2 rounded-lg border font-medium hover:opacity-80 transition disabled:opacity-40"
        >
          ← Previous
        </button>

        <div class="relative flex items-center gap-2">
          <span class="opacity-70 font-medium">Jump to:</span>

          <!-- Custom Hard-Width Chapter Dropdown with Full Multi-line Text Wrapping -->
          <div class="relative inline-block text-left" id="chapter-dropdown-wrapper">
            <!-- Hard-Width Trigger Button -->
            <button
              id="chapter-dropdown-btn"
              type="button"
              onclick="toggleChapterDropdown(event)"
              style="background-color: var(--reader-card); border-color: var(--reader-border); color: var(--reader-fg);"
              class="w-64 sm:w-80 flex items-center justify-between gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium hover:opacity-90 transition shadow-sm"
              aria-haspopup="listbox"
              aria-expanded="false"
            >
              <span id="chapter-dropdown-label" class="truncate text-left flex-1 font-semibold">
                Select chapter...
              </span>
              <svg id="chapter-dropdown-arrow" class="w-3.5 h-3.5 opacity-60 transition-transform duration-200 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            <!-- Hard-Width Dropdown Popover Menu with Word-Wrap Options -->
            <div
              id="chapter-dropdown-menu"
              style="background-color: var(--reader-card); border-color: var(--reader-border); color: var(--reader-fg);"
              class="hidden absolute left-0 sm:left-auto sm:right-0 mt-1.5 w-72 sm:w-96 rounded-xl border shadow-2xl z-50 overflow-hidden flex flex-col"
            >
              <!-- Live Search Filter -->
              <div class="p-2 border-b" style="border-color: var(--reader-border);">
                <input
                  id="chapter-dropdown-search"
                  type="text"
                  placeholder="🔍 Search # or title..."
                  oninput="filterChapterDropdown(this.value)"
                  style="background-color: var(--reader-bg); border-color: var(--reader-border); color: var(--reader-fg);"
                  class="w-full px-2.5 py-1.5 rounded-lg border text-xs outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              <!-- Scrollable Option List with Natural Multi-line Wrapping -->
              <div
                id="chapter-dropdown-list"
                class="max-h-80 overflow-y-auto divide-y"
                style="border-color: var(--reader-border);"
                role="listbox"
              ></div>
            </div>

            <!-- Hidden native select for backwards compatibility -->
            <select
              id="chapter-select"
              onchange="selectChapter(parseInt(this.value, 10))"
              class="sr-only"
              aria-hidden="true"
              tabindex="-1"
            ></select>
          </div>
        </div>

        <button
          id="next-btn-top"
          onclick="stepChapter(1)"
          style="background-color: var(--reader-card); border-color: var(--reader-border);"
          class="px-3.5 py-2 rounded-lg border font-medium hover:opacity-80 transition disabled:opacity-40"
        >
          Next →
        </button>
      </div>

      <!-- Chapter Content Mount Point -->
      <div id="chapter-viewport" class="space-y-10"></div>

      <!-- Bottom Pagination Bar -->
      <div id="bottom-pagination-bar" class="mt-10 pt-6 border-t flex items-center justify-between" style="border-color: var(--reader-border);">
        <button
          id="prev-btn-bottom"
          onclick="stepChapter(-1)"
          style="background-color: var(--reader-card); border-color: var(--reader-border);"
          class="px-5 py-3 rounded-xl border text-sm font-medium hover:opacity-80 transition disabled:opacity-40"
        >
          ← Previous Chapter
        </button>

        <button
          onclick="toggleModal('index-modal')"
          style="background-color: var(--reader-card); border-color: var(--reader-border);"
          class="px-4 py-3 rounded-xl border text-sm font-medium hover:opacity-80 transition"
        >
          📑 Chapter Index
        </button>

        <button
          id="next-btn-bottom"
          onclick="stepChapter(1)"
          style="background-color: var(--reader-accent); color: #fff;"
          class="px-5 py-3 rounded-xl text-sm font-semibold hover:opacity-90 transition disabled:opacity-40 shadow-sm"
        >
          Next Chapter →
        </button>
      </div>
    </div>
  </main>

  <!-- Chapter Index / Overview Modal -->
  <div
    id="index-modal"
    class="hidden fixed inset-0 z-50 flex items-center justify-center p-4"
    style="background-color: rgba(0, 0, 0, 0.65);"
  >
    <div
      style="background-color: var(--reader-card); border-color: var(--reader-border); max-height: 85vh;"
      class="w-full max-w-4xl rounded-2xl border shadow-2xl overflow-hidden flex flex-col"
    >
      <div class="p-4 border-b flex flex-wrap items-center justify-between gap-3" style="border-color: var(--reader-border);">
        <div>
          <h2 class="text-base font-bold">📑 Chapter Overview &amp; Reading History (`reading_progress.json`)</h2>
          <p id="index-modal-subtitle" class="text-xs opacity-70"></p>
        </div>
        <button onclick="toggleModal('index-modal')" class="px-3 py-1 rounded-lg border text-xs font-medium" style="border-color: var(--reader-border);">
          ✕ Close
        </button>
      </div>

      <!-- Last Read Banner inside Index Modal -->
      <div
        id="last-read-banner"
        style="background-color: color-mix(in srgb, var(--reader-accent) 12%, var(--reader-bg)); border-color: var(--reader-border);"
        class="px-4 py-2.5 border-b flex flex-wrap items-center justify-between gap-2 text-xs"
      >
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded font-bold uppercase" style="background-color: var(--reader-accent); color: #fff;">📍 Last Read</span>
          <span id="last-read-banner-text" class="font-semibold">000 — Synopsis</span>
        </div>
        <button
          onclick="resumeLastRead()"
          class="font-semibold hover:underline"
          style="color: var(--reader-accent);"
        >
          Resume Reading →
        </button>
      </div>

      <div class="p-3 border-b flex flex-wrap items-center justify-between gap-3 text-xs" style="border-color: var(--reader-border); background-color: var(--reader-bg);">
        <input
          id="chapter-search"
          type="text"
          placeholder="Search chapter number or title..."
          oninput="renderChapterDirectory()"
          style="background-color: var(--reader-card); border-color: var(--reader-border); color: var(--reader-fg);"
          class="flex-1 min-w-[200px] px-3 py-2 rounded-lg border"
        />
        <div class="flex flex-wrap items-center gap-1.5">
          <button onclick="setIndexFilter('all')" id="filter-all" class="px-2.5 py-1.5 rounded-lg border font-semibold" style="border-color: var(--reader-border); background-color: var(--reader-card);">
            All
          </button>
          <button onclick="setIndexFilter('translated')" id="filter-translated" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            ✅ Translated
          </button>
          <button onclick="setIndexFilter('read')" id="filter-read" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            📖 Read
          </button>
          <button onclick="setIndexFilter('unread')" id="filter-unread" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            🆕 Unread EN
          </button>
          <button onclick="setIndexFilter('raw')" id="filter-raw" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            ⏳ Pending CN
          </button>
        </div>
      </div>

      <div id="chapter-directory-grid" style="max-height: 55vh; overflow-y: auto;" class="p-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 text-xs"></div>
    </div>
  </div>

  <!-- Full Scrollable Glossary Modal -->
  <div
    id="glossary-modal"
    class="hidden fixed inset-0 z-50 flex items-center justify-center p-4"
    style="background-color: rgba(0, 0, 0, 0.65);"
  >
    <div
      style="background-color: var(--reader-card); border-color: var(--reader-border);"
      class="w-full max-w-4xl rounded-2xl border shadow-2xl overflow-hidden flex flex-col"
    >
      <div class="p-4 border-b flex items-center justify-between" style="border-color: var(--reader-border);">
        <div>
          <h2 class="text-base font-bold">👥 Master Glossary (`GLOSSARY.md`)</h2>
          <p class="text-xs opacity-70">Click any character or sect in the story text to jump directly to their card</p>
        </div>
        <button onclick="toggleModal('glossary-modal')" class="px-3 py-1 rounded-lg border text-xs font-medium" style="border-color: var(--reader-border);">
          ✕ Close
        </button>
      </div>

      <div class="p-3 border-b flex flex-wrap items-center justify-between gap-2 text-xs" style="border-color: var(--reader-border); background-color: var(--reader-bg);">
        <input
          id="glossary-search"
          type="text"
          placeholder="Filter characters, locations, sects, realms..."
          oninput="renderGlossaryList()"
          style="background-color: var(--reader-card); border-color: var(--reader-border); color: var(--reader-fg);"
          class="flex-1 min-w-[200px] px-3 py-1.5 rounded-lg border"
        />
        <div class="flex items-center gap-1.5">
          <button onclick="setGlossaryTab('all')" id="gtab-all" class="px-2.5 py-1.5 rounded-lg border font-semibold" style="border-color: var(--reader-border); background-color: var(--reader-card);">
            All
          </button>
          <button onclick="setGlossaryTab('Characters')" id="gtab-Characters" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            Characters
          </button>
          <button onclick="setGlossaryTab('Locations')" id="gtab-Locations" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            Locations &amp; Sects
          </button>
          <button onclick="setGlossaryTab('Realms')" id="gtab-Realms" class="px-2.5 py-1.5 rounded-lg border opacity-75" style="border-color: var(--reader-border);">
            Realms &amp; Manuals
          </button>
        </div>
      </div>

      <div
        id="glossary-scroll-container"
        style="max-height: 60vh; overflow-y: auto;"
        class="p-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs"
      ></div>
    </div>
  </div>

  <!-- Floating Hover Tooltip Element -->
  <div
    id="floating-tooltip"
    style="background-color: var(--reader-tooltip-bg); color: var(--reader-tooltip-fg); border-color: var(--reader-accent);"
    class="opacity-0 fixed max-w-xs px-3.5 py-2.5 rounded-xl border shadow-xl text-xs leading-relaxed"
  >
    <div id="tooltip-badge" class="font-bold uppercase tracking-wider text-xs mb-0.5" style="color: var(--reader-accent);"></div>
    <div id="tooltip-body"></div>
  </div>

  <script>
    // Bust browser cache dynamically so new chapters and live rebuilds appear immediately
    document.write('<script src="chapters_data.js?v=' + Date.now() + '"><\\/script>');
  </script>
  <script>
    const GLOSSARY_ENTRIES = window.READER_DATA.glossaryEntries || [];
    const TRANSLATED_CHAPTERS = window.READER_DATA.translatedChapters || [];
    const ALL_CHAPTERS_META = window.READER_DATA.allChaptersMeta || [];

    // Load initial DB state from `reading_progress.json` (embedded + synced via /api/progress)
    let progressDB = window.READER_DATA.initialProgress || {
      last_read_code: "000",
      last_read_title: "Synopsis & Introduction",
      read_chapters: ["000"],
      scroll_positions: {},
      settings: {}
    };
    let readChaptersSet = new Set(progressDB.read_chapters || []);

    let currentChapterIdx = 0;
    let scrollMode = "paginated";
    let currentFilter = "all";
    let currentGlossaryTab = "all";
    let highlightedGlossaryId = null;
    let saveTimer = null;

    function escapeHtml(str) {
      if (!str) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    async function fetchLatestProgressFromServer() {
      let serverLoaded = false;
      if (window.location.protocol.startsWith("http")) {
        try {
          const resp = await fetch("/api/progress", { cache: "no-store" });
          if (resp.ok) {
            const serverData = await resp.json();
            progressDB = serverData;
            readChaptersSet = new Set(progressDB.read_chapters || []);
            serverLoaded = true;
          }
        } catch (_) {}
      }
      if (!serverLoaded) {
        // Fallback to localStorage if opened via file:// or static web hosting (e.g. GitHub Pages)
        const localRaw = localStorage.getItem("reader_progress_db");
        if (localRaw) {
          try {
            progressDB = JSON.parse(localRaw);
            readChaptersSet = new Set(progressDB.read_chapters || []);
          } catch (_) {}
        }
      }
    }

    function syncProgressToDisk() {
      progressDB.read_chapters = Array.from(readChaptersSet);
      localStorage.setItem("reader_progress_db", JSON.stringify(progressDB));

      if (window.location.protocol.startsWith("http")) {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          fetch("/api/progress", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(progressDB)
          }).catch(() => {});
        }, 150);
      }
      updateProgressCounters();
    }

    async function triggerLiveRebuild() {
      const btn = document.getElementById("rebuild-btn");
      btn.textContent = "⏳ Syncing...";
      if (!window.location.protocol.startsWith("http")) {
        const url = new URL(window.location.href);
        url.searchParams.set("t", Date.now());
        window.location.href = url.toString();
        return;
      }
      try {
        await fetch("/api/rebuild", { method: "POST" });
      } catch (_) {}
      // Force hard refresh with cache-busting timestamp
      const url = new URL(window.location.href);
      url.searchParams.set("t", Date.now());
      window.location.href = url.toString();
    }

    /* --- Custom Hard-Width Chapter Dropdown Logic --- */
    function toggleChapterDropdown(event) {
      if (event) event.stopPropagation();
      const menu = document.getElementById("chapter-dropdown-menu");
      const arrow = document.getElementById("chapter-dropdown-arrow");
      const btn = document.getElementById("chapter-dropdown-btn");
      if (!menu) return;

      const isHidden = menu.classList.contains("hidden");
      if (isHidden) {
        menu.classList.remove("hidden");
        if (arrow) arrow.classList.add("rotate-180");
        if (btn) btn.setAttribute("aria-expanded", "true");
        const searchInput = document.getElementById("chapter-dropdown-search");
        if (searchInput) {
          searchInput.value = "";
          setTimeout(() => searchInput.focus(), 50);
        }
        renderChapterDropdownList();
        scrollActiveDropdownItemIntoView();
      } else {
        closeChapterDropdown();
      }
    }

    function closeChapterDropdown() {
      const menu = document.getElementById("chapter-dropdown-menu");
      const arrow = document.getElementById("chapter-dropdown-arrow");
      const btn = document.getElementById("chapter-dropdown-btn");
      if (menu) menu.classList.add("hidden");
      if (arrow) arrow.classList.remove("rotate-180");
      if (btn) btn.setAttribute("aria-expanded", "false");
    }

    function renderChapterDropdownList(filterQuery = "") {
      const listEl = document.getElementById("chapter-dropdown-list");
      if (!listEl) return;
      const q = filterQuery.trim().toLowerCase();

      const items = TRANSLATED_CHAPTERS.map((ch, idx) => ({ ch, idx })).filter(({ ch }) => {
        if (!q) return true;
        return (
          ch.code.toLowerCase().includes(q) ||
          (ch.titleEn && ch.titleEn.toLowerCase().includes(q)) ||
          (ch.titleZh && ch.titleZh.toLowerCase().includes(q))
        );
      });

      if (items.length === 0) {
        listEl.innerHTML = `<div class="p-4 text-center text-xs opacity-60">No chapters found for "${escapeHtml(q)}"</div>`;
        return;
      }

      listEl.innerHTML = items.map(({ ch, idx }) => {
        const isActive = idx === currentChapterIdx;
        const isRead = readChaptersSet.has(ch.code);
        const isLast = ch.code === progressDB.last_read_code;

        const badgeIcon = isLast ? "📍" : isRead ? "✓" : "○";
        const badgeColor = isLast
          ? "color: var(--reader-accent);"
          : isRead
          ? "color: #10b981;"
          : "opacity: 0.5;";

        const activeBg = isActive ? "background-color: var(--reader-monologue-bg);" : "";
        const activeText = isActive ? "color: var(--reader-accent); font-weight: 700;" : "";

        return `
          <div
            id="dropdown-opt-${idx}"
            onclick="onSelectDropdownChapter(${idx})"
            class="px-3.5 py-2.5 cursor-pointer text-left transition hover:opacity-80 flex items-start gap-2.5 text-xs"
            style="${activeBg} border-color: var(--reader-border); word-break: break-word; overflow-wrap: break-word; white-space: normal;"
            role="option"
            aria-selected="${isActive}"
          >
            <span class="shrink-0 mt-0.5 text-xs font-semibold" style="${badgeColor}">${badgeIcon}</span>
            <div class="flex-1 min-w-0" style="word-break: break-word; overflow-wrap: break-word; white-space: normal;">
              <div class="font-semibold leading-snug" style="${activeText} word-break: break-word; overflow-wrap: break-word; white-space: normal;">
                <span class="opacity-60 text-[11px] font-mono mr-1">#${ch.code}</span>
                <span>${escapeHtml(ch.titleEn)}</span>
              </div>
              ${ch.titleZh ? `<div class="opacity-60 text-[11px] mt-0.5" style="word-break: break-word; overflow-wrap: break-word; white-space: normal;">${escapeHtml(ch.titleZh)}</div>` : ''}
            </div>
          </div>
        `;
      }).join("");
    }

    function scrollActiveDropdownItemIntoView() {
      setTimeout(() => {
        const activeItem = document.getElementById(`dropdown-opt-${currentChapterIdx}`);
        if (activeItem) {
          activeItem.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      }, 50);
    }

    function filterChapterDropdown(text) {
      renderChapterDropdownList(text);
    }

    function onSelectDropdownChapter(idx) {
      selectChapter(idx);
      closeChapterDropdown();
    }

    function updateChapterDropdownLabel() {
      const labelEl = document.getElementById("chapter-dropdown-label");
      const ch = TRANSLATED_CHAPTERS[currentChapterIdx];
      if (!labelEl || !ch) return;

      const isRead = readChaptersSet.has(ch.code);
      const isLast = ch.code === progressDB.last_read_code;
      const prefix = isLast ? "📍 " : isRead ? "✓ " : "○ ";

      labelEl.textContent = `${prefix}#${ch.code} — ${ch.titleEn}`;
    }

    async function initReader() {
      await fetchLatestProgressFromServer();

      const s = progressDB.settings || {};
      const savedTheme = s.theme || localStorage.getItem("reader_theme") || "midnight";
      const savedMono = s.monologueStyle || localStorage.getItem("reader_mono") || "pill";
      const savedSpk = s.speakerMode || localStorage.getItem("reader_spk") || "hidden";
      const savedSlang = s.slangMode || localStorage.getItem("reader_slang") || "hidden";
      const savedFs = s.fontSize || localStorage.getItem("reader_fs") || "18";
      const savedFont = s.fontFamily || localStorage.getItem("reader_font") || "sans";
      scrollMode = s.scrollMode || localStorage.getItem("reader_scrollMode") || "paginated";

      setTheme(savedTheme, false);
      setMonologueStyle(savedMono, false);
      setSpeakerMode(savedSpk, false);
      setSlangMode(savedSlang, false);
      setFontSize(savedFs, false);
      setFontFamily(savedFont, false);
      document.getElementById("fs-slider").value = savedFs;

      if (progressDB.last_read_code) {
        const foundIdx = TRANSLATED_CHAPTERS.findIndex(c => c.code === progressDB.last_read_code);
        if (foundIdx >= 0) currentChapterIdx = foundIdx;
      }

      document.getElementById("nav-glossary-count").textContent = `Glossary (${GLOSSARY_ENTRIES.length})`;
      updateProgressCounters();
      setScrollMode(scrollMode, false);

      // Restore scroll offset for the last read chapter
      const activeCh = TRANSLATED_CHAPTERS[currentChapterIdx];
      if (activeCh && progressDB.scroll_positions && progressDB.scroll_positions[activeCh.code]) {
        setTimeout(() => {
          window.scrollTo({ top: progressDB.scroll_positions[activeCh.code], behavior: "instant" });
        }, 50);
      }
    }

    function updateProgressCounters() {
      const totalTranslated = TRANSLATED_CHAPTERS.length;
      const totalAll = ALL_CHAPTERS_META.length;
      const totalRead = readChaptersSet.size;
      const unreadTranslated = TRANSLATED_CHAPTERS.filter(c => !readChaptersSet.has(c.code)).length;

      document.getElementById("nav-chapters-count").textContent = `Chapters (${totalTranslated}/${totalAll})`;
      document.getElementById("header-read-counter").textContent = `${totalRead} Read`;
      document.getElementById("index-modal-subtitle").textContent =
        `${totalRead} Read • ${totalTranslated} Translated • ${totalAll - totalTranslated} Pending in raw_chapters/`;

      document.getElementById("filter-all").textContent = `All (${totalAll})`;
      document.getElementById("filter-translated").textContent = `✅ Translated (${totalTranslated})`;
      document.getElementById("filter-read").textContent = `📖 Read (${totalRead})`;
      document.getElementById("filter-unread").textContent = `🆕 Unread EN (${unreadTranslated})`;
      document.getElementById("filter-raw").textContent = `⏳ Pending CN (${totalAll - totalTranslated})`;

      // Update Last Read Banner
      const lastCode = progressDB.last_read_code || "000";
      const lastMeta = ALL_CHAPTERS_META.find(m => m.code === lastCode);
      document.getElementById("last-read-banner-text").textContent =
        lastMeta ? `#${lastMeta.code} — ${lastMeta.titleEn} (${lastMeta.titleZh})` : `#${lastCode}`;

      // Update Top Chapter Dropdown & label
      updateChapterDropdownLabel();
      renderChapterDropdownList();

      // Keep hidden native select in sync for API compatibility
      const selectEl = document.getElementById("chapter-select");
      if (selectEl) {
        selectEl.innerHTML = TRANSLATED_CHAPTERS.map((ch, idx) => {
          const isRead = readChaptersSet.has(ch.code);
          const isLast = ch.code === progressDB.last_read_code;
          const prefix = isLast ? "📍 " : isRead ? "✓ " : "○ ";
          return `<option value="${idx}" ${idx === currentChapterIdx ? 'selected' : ''}>${prefix}${ch.code} — ${ch.titleEn}</option>`;
        }).join("");
        selectEl.value = String(currentChapterIdx);
      }
    }

    function toggleChapterReadStatus(code, event) {
      if (event) event.stopPropagation();
      if (readChaptersSet.has(code)) {
        readChaptersSet.delete(code);
      } else {
        readChaptersSet.add(code);
      }
      syncProgressToDisk();
      renderViewport();
      if (!document.getElementById("index-modal").classList.contains("hidden")) {
        renderChapterDirectory();
      }
    }

    function resumeLastRead() {
      const foundIdx = TRANSLATED_CHAPTERS.findIndex(c => c.code === progressDB.last_read_code);
      if (foundIdx >= 0) {
        selectChapter(foundIdx);
        toggleModal("index-modal");
      }
    }

    function renderChapterCard(ch) {
      const isRead = readChaptersSet.has(ch.code);
      const isLast = ch.code === progressDB.last_read_code;
      return `
        <article
          style="background-color: var(--reader-card); border-color: var(--reader-border);"
          class="rounded-2xl border p-6 sm:p-10 shadow-sm"
        >
          <div class="border-b pb-5 mb-6" style="border-color: var(--reader-border);">
            <div class="flex flex-wrap items-center justify-between gap-2 text-xs mb-2">
              <div class="flex items-center gap-2 opacity-75">
                <span>FILE: ${ch.filename}</span>
                <span>•</span>
                <span>${ch.words}</span>
              </div>
              <div class="flex items-center gap-2">
                ${isLast ? `<span class="px-2 py-0.5 rounded font-semibold" style="background-color: var(--reader-monologue-bg); color: var(--reader-accent);">📍 Last Read</span>` : ''}
                <button
                  onclick="toggleChapterReadStatus('${ch.code}', event)"
                  style="border-color: var(--reader-border); background-color: ${isRead ? 'rgba(16, 185, 129, 0.16)' : 'var(--reader-bg)'}; color: ${isRead ? '#10b981' : 'var(--reader-muted)'};"
                  class="px-2.5 py-1 rounded-lg border font-semibold transition hover:opacity-85"
                >
                  ${isRead ? '✓ Marked as Read' : '○ Mark as Read'}
                </button>
              </div>
            </div>
            <h1 class="text-2xl sm:text-3xl font-bold tracking-tight mb-1">${ch.titleEn}</h1>
            <div class="text-sm opacity-75">${ch.titleZh}</div>
          </div>

          <div class="novel-prose">
            ${ch.html}
          </div>
        </article>
      `;
    }

    function renderViewport() {
      const viewport = document.getElementById("chapter-viewport");
      const topBar = document.getElementById("top-pagination-bar");
      const bottomBar = document.getElementById("bottom-pagination-bar");
      const headerTitle = document.getElementById("header-chapter-title");

      if (TRANSLATED_CHAPTERS.length === 0) {
        headerTitle.textContent = "No Translated Chapters Yet";
        topBar.classList.add("hidden");
        bottomBar.classList.add("hidden");
        viewport.innerHTML = `
          <div style="background-color: var(--reader-card); border-color: var(--reader-border);" class="rounded-2xl border p-8 text-center">
            <h2 class="text-xl font-bold mb-2">No translated chapters found in <code>translated_chapters/</code></h2>
            <p class="text-sm opacity-75">Ask Antigravity to translate a chapter (e.g. "Translate Chapter 1") and click <strong>🔄 Sync Files</strong>!</p>
          </div>
        `;
        return;
      }

      if (scrollMode === "paginated") {
        const ch = TRANSLATED_CHAPTERS[currentChapterIdx] || TRANSLATED_CHAPTERS[0];
        // Record as Last Read & automatically mark as Read in reading_progress.json
        progressDB.last_read_code = ch.code;
        progressDB.last_read_title = ch.titleEn;
        readChaptersSet.add(ch.code);
        syncProgressToDisk();

        viewport.innerHTML = renderChapterCard(ch);
        headerTitle.textContent = ch.titleEn;
        topBar.classList.remove("hidden");
        bottomBar.classList.remove("hidden");

        const disablePrev = currentChapterIdx <= 0;
        const disableNext = currentChapterIdx >= TRANSLATED_CHAPTERS.length - 1;
        document.getElementById("prev-btn-top").disabled = disablePrev;
        document.getElementById("prev-btn-bottom").disabled = disablePrev;
        document.getElementById("next-btn-top").disabled = disableNext;
        document.getElementById("next-btn-bottom").disabled = disableNext;
      } else {
        headerTitle.textContent = `Infinite Scroll (${TRANSLATED_CHAPTERS.length} Translated Chapters)`;
        topBar.classList.add("hidden");
        bottomBar.classList.add("hidden");
        viewport.innerHTML = TRANSLATED_CHAPTERS.map((ch, idx) => `
          <div>
            ${idx > 0 ? `
              <div class="flex items-center gap-4 my-8 opacity-60 text-xs font-semibold uppercase tracking-widest">
                <div class="flex-1 border-t" style="border-color: var(--reader-border);"></div>
                <span>♾️ Chapter ${ch.code}</span>
                <div class="flex-1 border-t" style="border-color: var(--reader-border);"></div>
              </div>
            ` : ''}
            ${renderChapterCard(ch)}
          </div>
        `).join("");
      }

      bindTooltips();
    }

    function selectChapter(tIdx) {
      if (tIdx === null || tIdx === undefined) return;
      currentChapterIdx = tIdx;
      updateChapterDropdownLabel();
      if (scrollMode !== "paginated") {
        setScrollMode("paginated");
      } else {
        renderViewport();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }

    function stepChapter(delta) {
      const next = currentChapterIdx + delta;
      if (next >= 0 && next < TRANSLATED_CHAPTERS.length) {
        currentChapterIdx = next;
        updateChapterDropdownLabel();
        renderViewport();
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }

    function saveSetting(key, val) {
      progressDB.settings = progressDB.settings || {};
      progressDB.settings[key] = val;
      syncProgressToDisk();
    }

    function setScrollMode(mode, persist = true) {
      scrollMode = mode;
      localStorage.setItem("reader_scrollMode", mode);
      if (persist) saveSetting("scrollMode", mode);
      const pagBtn = document.getElementById("mode-paginated-btn");
      const infBtn = document.getElementById("mode-infinite-btn");

      if (mode === "paginated") {
        pagBtn.style.backgroundColor = "var(--reader-card)";
        pagBtn.style.boxShadow = "0 1px 2px rgba(0,0,0,0.08)";
        pagBtn.classList.remove("opacity-70");
        infBtn.style.backgroundColor = "transparent";
        infBtn.style.boxShadow = "none";
        infBtn.classList.add("opacity-70");
      } else {
        infBtn.style.backgroundColor = "var(--reader-card)";
        infBtn.style.boxShadow = "0 1px 2px rgba(0,0,0,0.08)";
        infBtn.classList.remove("opacity-70");
        pagBtn.style.backgroundColor = "transparent";
        pagBtn.style.boxShadow = "none";
        pagBtn.classList.add("opacity-70");
      }
      renderViewport();
    }

    function setTheme(theme, persist = true) {
      document.body.setAttribute("data-reader-theme", theme);
      localStorage.setItem("reader_theme", theme);
      if (persist) saveSetting("theme", theme);
    }

    function setFontFamily(family, persist = true) {
      localStorage.setItem("reader_font", family);
      if (persist) saveSetting("fontFamily", family);
      const prose = document.getElementById("chapter-viewport");
      if (family === "serif") {
        prose.classList.add("font-serif");
        prose.classList.remove("font-sans");
      } else {
        prose.classList.add("font-sans");
        prose.classList.remove("font-serif");
      }
    }

    function setFontSize(sizePx, persist = true) {
      localStorage.setItem("reader_fs", sizePx);
      if (persist) saveSetting("fontSize", String(sizePx));
      document.documentElement.style.setProperty("--reader-fs", sizePx + "px");
      document.getElementById("fs-label").textContent = sizePx + "px";
    }

    function setMonologueStyle(style, persist = true) {
      localStorage.setItem("reader_mono", style);
      if (persist) saveSetting("monologueStyle", style);
      document.body.setAttribute("data-monologue-style", style);
      ["pill", "italic"].forEach(s => {
        const btn = document.getElementById("mono-" + s);
        if (s === style) {
          btn.style.borderColor = "var(--reader-accent)";
          btn.classList.add("font-semibold");
          btn.classList.remove("opacity-75");
        } else {
          btn.style.borderColor = "var(--reader-border)";
          btn.classList.remove("font-semibold");
          btn.classList.add("opacity-75");
        }
      });
    }

    function setSpeakerMode(mode, persist = true) {
      localStorage.setItem("reader_spk", mode);
      if (persist) saveSetting("speakerMode", mode);
      document.body.setAttribute("data-speaker-mode", mode);
      ["hidden", "hover", "pill"].forEach(m => {
        const btn = document.getElementById("spk-" + m);
        if (m === mode) {
          btn.style.borderColor = "var(--reader-accent)";
          btn.classList.add("font-semibold");
          btn.classList.remove("opacity-75");
        } else {
          btn.style.borderColor = "var(--reader-border)";
          btn.classList.remove("font-semibold");
          btn.classList.add("opacity-75");
        }
      });
    }

    function setSlangMode(mode, persist = true) {
      localStorage.setItem("reader_slang", mode);
      if (persist) saveSetting("slangMode", mode);
      document.body.setAttribute("data-slang-mode", mode);
      ["hidden", "hover", "pill"].forEach(m => {
        const btn = document.getElementById("slang-" + m);
        if (m === mode) {
          btn.style.borderColor = "var(--reader-accent)";
          btn.classList.add("font-semibold");
          btn.classList.remove("opacity-75");
        } else {
          btn.style.borderColor = "var(--reader-border)";
          btn.classList.remove("font-semibold");
          btn.classList.add("opacity-75");
        }
      });
    }

    function toggleModal(id) {
      const el = document.getElementById(id);
      el.classList.toggle("hidden");
      if (id === "index-modal" && !el.classList.contains("hidden")) {
        renderChapterDirectory();
      }
    }

    function openGlossary(targetId) {
      const modal = document.getElementById("glossary-modal");
      modal.classList.remove("hidden");
      document.getElementById("glossary-search").value = "";
      setGlossaryTab("all", targetId || null);

      if (targetId) {
        setTimeout(() => {
          const card = document.getElementById("gcard-" + targetId);
          if (card) {
            card.scrollIntoView({ behavior: "smooth", block: "center" });
          }
        }, 60);
      }
    }

    function setGlossaryTab(tab, focusId = null) {
      currentGlossaryTab = tab;
      highlightedGlossaryId = focusId;
      ["all", "Characters", "Locations", "Realms"].forEach(t => {
        const btn = document.getElementById("gtab-" + t);
        if (t === tab) {
          btn.style.backgroundColor = "var(--reader-card)";
          btn.classList.add("font-semibold");
          btn.classList.remove("opacity-75");
        } else {
          btn.style.backgroundColor = "transparent";
          btn.classList.remove("font-semibold");
          btn.classList.add("opacity-75");
        }
      });
      renderGlossaryList();
    }

    function renderGlossaryList() {
      const q = (document.getElementById("glossary-search").value || "").toLowerCase().trim();
      const container = document.getElementById("glossary-scroll-container");

      const filtered = GLOSSARY_ENTRIES.filter(item => {
        if (currentGlossaryTab !== "all" && item.category !== currentGlossaryTab) return false;
        if (!q) return true;
        return (
          item.name.toLowerCase().includes(q) ||
          item.zh.toLowerCase().includes(q) ||
          item.titles.toLowerCase().includes(q) ||
          item.role.toLowerCase().includes(q) ||
          item.desc.toLowerCase().includes(q)
        );
      });

      container.innerHTML = filtered.map(item => {
        const isHighlighted = item.id === highlightedGlossaryId;
        return `
          <div
            id="gcard-${item.id}"
            style="border-color: var(--reader-border); background-color: var(--reader-bg);"
            class="p-3.5 rounded-xl border transition ${isHighlighted ? 'glossary-card-active' : ''}"
          >
            <div class="flex items-center justify-between gap-2 mb-1">
              <span class="font-bold text-sm">${item.name} <span class="opacity-75 font-normal">(${item.zh})</span></span>
              <span class="px-2 py-0.5 rounded text-xs font-medium shrink-0" style="background-color: var(--reader-monologue-bg);">
                ${item.sex}
              </span>
            </div>
            ${item.titles ? `<div class="opacity-75 mb-1"><strong>Titles:</strong> ${item.titles}</div>` : ''}
            ${item.role ? `<div class="opacity-75 mb-1.5"><strong>Role:</strong> ${item.role}</div>` : ''}
            <p class="opacity-90 leading-relaxed">${item.desc}</p>
          </div>
        `;
      }).join("");
    }

    function setIndexFilter(filter) {
      currentFilter = filter;
      ["all", "translated", "read", "unread", "raw"].forEach(f => {
        const btn = document.getElementById("filter-" + f);
        if (f === filter) {
          btn.style.backgroundColor = "var(--reader-card)";
          btn.classList.add("font-semibold");
          btn.classList.remove("opacity-75");
        } else {
          btn.style.backgroundColor = "transparent";
          btn.classList.remove("font-semibold");
          btn.classList.add("opacity-75");
        }
      });
      renderChapterDirectory();
    }

    function renderChapterDirectory() {
      const q = (document.getElementById("chapter-search").value || "").toLowerCase().trim();
      const grid = document.getElementById("chapter-directory-grid");

      const filtered = ALL_CHAPTERS_META.filter(ch => {
        const isRead = readChaptersSet.has(ch.code);
        if (currentFilter === "translated" && ch.status !== "translated") return false;
        if (currentFilter === "raw" && ch.status !== "raw") return false;
        if (currentFilter === "read" && !isRead) return false;
        if (currentFilter === "unread" && (ch.status !== "translated" || isRead)) return false;

        if (!q) return true;
        return (
          ch.code.toLowerCase().includes(q) ||
          ch.titleZh.toLowerCase().includes(q) ||
          ch.titleEn.toLowerCase().includes(q)
        );
      });

      grid.innerHTML = filtered.map(ch => {
        const isTranslated = ch.status === "translated";
        const isRead = readChaptersSet.has(ch.code);
        const isLast = ch.code === progressDB.last_read_code;

        return `
          <div
            onclick="${isTranslated ? `selectChapter(${ch.tIdx}); toggleModal('index-modal');` : ''}"
            style="background-color: var(--reader-bg); border-color: ${isLast ? 'var(--reader-accent)' : 'var(--reader-border)'};"
            class="p-3 rounded-xl border flex flex-col justify-between transition ${isTranslated ? 'cursor-pointer hover:opacity-85' : 'opacity-65'}"
          >
            <div>
              <div class="flex items-center justify-between gap-1.5 mb-1">
                <div class="flex items-center gap-1.5">
                  <span class="font-bold">#${ch.code}</span>
                  ${isLast ? `<span class="px-1.5 py-0.5 rounded text-xs font-bold" style="background-color: var(--reader-accent); color: #fff;">📍 LAST</span>` : ''}
                </div>
                <div class="flex items-center gap-1">
                  <button
                    onclick="toggleChapterReadStatus('${ch.code}', event)"
                    title="Click to toggle Read / Unread"
                    class="px-2 py-0.5 rounded text-xs font-medium border"
                    style="border-color: var(--reader-border); background-color: ${isRead ? 'rgba(16, 185, 129, 0.18)' : 'transparent'}; color: ${isRead ? '#10b981' : 'var(--reader-muted)'};"
                  >
                    ${isRead ? '✓ Read' : '○ Unread'}
                  </button>
                </div>
              </div>
              <div class="font-semibold truncate">${ch.titleEn}</div>
              <div class="opacity-70 truncate">${ch.titleZh}</div>
            </div>
            <div class="mt-2 pt-1.5 border-t flex justify-between opacity-65" style="border-color: var(--reader-border);">
              <span>${ch.chars.toLocaleString()} CN chars</span>
              <span>${isTranslated ? `✅ ${ch.words.toLocaleString()} words →` : '⏳ Raw CN'}</span>
            </div>
          </div>
        `;
      }).join("");
    }

    function bindTooltips() {
      const tooltip = document.getElementById("floating-tooltip");
      const badge = document.getElementById("tooltip-badge");
      const body = document.getElementById("tooltip-body");

      document.querySelectorAll(".has-char-tooltip").forEach(el => {
        el.addEventListener("mouseenter", (e) => {
          badge.textContent = el.getAttribute("data-tip-type") || "Character";
          body.textContent = el.getAttribute("data-tip-text") || "";
          tooltip.classList.remove("opacity-0");
          tooltip.classList.add("opacity-100");
          positionTooltip(e);
        });
        el.addEventListener("mousemove", positionTooltip);
        el.addEventListener("mouseleave", () => {
          tooltip.classList.remove("opacity-100");
          tooltip.classList.add("opacity-0");
        });
      });

      document.querySelectorAll(".has-tooltip").forEach(el => {
        el.addEventListener("mouseenter", (e) => {
          const isSpeaker = el.classList.contains("unattributed-dialogue");
          const isSlang = el.classList.contains("cultural-note");
          const speakerMode = document.body.getAttribute("data-speaker-mode");
          const slangMode = document.body.getAttribute("data-slang-mode");

          if (isSpeaker && speakerMode === "hidden") return;
          if (isSlang && slangMode === "hidden") return;

          badge.textContent = el.getAttribute("data-tip-type") || "Note";
          body.textContent = el.getAttribute("data-tip-text") || "";
          tooltip.classList.remove("opacity-0");
          tooltip.classList.add("opacity-100");
          positionTooltip(e);
        });
        el.addEventListener("mousemove", positionTooltip);
        el.addEventListener("mouseleave", () => {
          tooltip.classList.remove("opacity-100");
          tooltip.classList.add("opacity-0");
        });
      });
    }

    function positionTooltip(e) {
      const tooltip = document.getElementById("floating-tooltip");
      const pad = 14;
      let x = e.clientX + pad;
      let y = e.clientY + pad;
      const rect = tooltip.getBoundingClientRect();
      if (x + rect.width > window.innerWidth - 16) {
        x = e.clientX - rect.width - pad;
      }
      if (y + rect.height > window.innerHeight - 16) {
        y = e.clientY - rect.height - pad;
      }
      tooltip.style.left = x + "px";
      tooltip.style.top = y + "px";
    }

    let scrollSaveTimeout = null;
    window.addEventListener("scroll", () => {
      const docHeight = document.documentElement.scrollHeight - window.innerHeight;
      const pct = docHeight > 0 ? Math.min(100, Math.max(5, (window.scrollY / docHeight) * 100)) : 100;
      document.getElementById("reading-progress").style.width = pct + "%";

      if (scrollMode === "paginated" && TRANSLATED_CHAPTERS[currentChapterIdx]) {
        clearTimeout(scrollSaveTimeout);
        scrollSaveTimeout = setTimeout(() => {
          const code = TRANSLATED_CHAPTERS[currentChapterIdx].code;
          progressDB.scroll_positions = progressDB.scroll_positions || {};
          progressDB.scroll_positions[code] = Math.round(window.scrollY);
          syncProgressToDisk();
        }, 400);
      }
    });

    document.addEventListener("click", (e) => {
      const wrapper = document.getElementById("chapter-dropdown-wrapper");
      if (wrapper && !wrapper.contains(e.target)) {
        closeChapterDropdown();
      }
    });

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeChapterDropdown();
      }
      const indexHidden = document.getElementById("index-modal").classList.contains("hidden");
      const glossaryHidden = document.getElementById("glossary-modal").classList.contains("hidden");
      if (scrollMode === "paginated" && indexHidden && glossaryHidden) {
        if (e.key === "ArrowLeft") stepChapter(-1);
        if (e.key === "ArrowRight") stepChapter(1);
      }
    });

    initReader();
  </script>
</body>
</html>
"""


def build_site() -> tuple[int, int, int]:
    """
    Build `site/index.html`, `site/chapters_data.js`, `reader_icon.ico`, and `site/favicon.ico`
    from `GLOSSARY.md`, `translated_chapters/`, and `reading_progress.json`.
    """
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    TRANSLATED_CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate custom Windows .ico icon in project root and site favicon
    if not ICON_FILE.exists():
        generate_ico_file(ICON_FILE)
    generate_ico_file(SITE_DIR / "favicon.ico")

    progress_data = load_progress_db()
    glossary_entries, auto_links = parse_glossary(GLOSSARY_FILE)
    processor = InlineProcessor(glossary_entries, auto_links)
    all_chapters_meta, translated_chapters = collect_all_chapters(
        RAW_CHAPTERS_DIR, TRANSLATED_CHAPTERS_DIR, processor
    )

    payload = {
        "glossaryEntries": glossary_entries,
        "allChaptersMeta": all_chapters_meta,
        "translatedChapters": translated_chapters,
        "initialProgress": progress_data,
    }

    data_js = "window.READER_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    (SITE_DIR / "chapters_data.js").write_text(data_js, encoding="utf-8")
    (SITE_DIR / "index.html").write_text(READER_HTML_TEMPLATE, encoding="utf-8")

    return len(translated_chapters), len(all_chapters_meta), len(glossary_entries)


def find_available_port(start_port: int = 8080) -> int:
    for port in range(start_port, start_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def serve_site(port: int = 8080) -> None:
    """Run local HTTP server with `/api/progress` JSON DB sync and `/api/rebuild` support."""
    actual_port = find_available_port(port)

    class ReaderRequestHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(SITE_DIR), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            # Keep console output clean; only log rebuilds or errors
            pass

        def end_headers(self) -> None:
            # Prevent browser caching of any served files so fresh chapters and live rebuilds are always seen
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/api/progress"):
                self._send_json(200, load_progress_db())
                return
            if self.path.startswith("/api/rebuild"):
                try:
                    t_count, total_count, g_count = build_site()
                    print(
                        f"[Sync] Rebuilt site: {t_count}/{total_count} chapters, {g_count} glossary entries."
                    )
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "translated": t_count,
                            "total": total_count,
                            "glossary": g_count,
                        },
                    )
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
                return
            super().do_GET()

        def do_POST(self) -> None:
            if self.path.startswith("/api/progress"):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_API_BODY_BYTES:
                        self._send_json(400, {"error": "Invalid payload size"})
                        return
                    raw_body = self.rfile.read(length)
                    incoming = json.loads(raw_body.decode("utf-8"))
                    saved = save_progress_db(incoming)
                    self._send_json(200, saved)
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
                return

            if self.path.startswith("/api/rebuild"):
                try:
                    t_count, total_count, g_count = build_site()
                    print(
                        f"[Sync] Rebuilt site: {t_count}/{total_count} chapters, {g_count} glossary entries."
                    )
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "translated": t_count,
                            "total": total_count,
                            "glossary": g_count,
                        },
                    )
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
                return

            self._send_json(404, {"error": "Not found"})

    # Generate a Windows Internet Shortcut (.url) with the custom .ico icon so the user has an iconized launcher
    shortcut_url_file = ROOT_DIR / "Open Reader (Icon).url"
    url = f"http://127.0.0.1:{actual_port}/index.html"
    shortcut_content = (
        "[InternetShortcut]\r\n"
        f"URL={url}\r\n"
        f"IconFile={ICON_FILE}\r\n"
        "IconIndex=0\r\n"
    )
    shortcut_url_file.write_text(shortcut_content, encoding="utf-8")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", actual_port), ReaderRequestHandler) as httpd:
        print(f"  - Progress DB:                  {PROGRESS_DB_FILE}")
        print(f"  - Custom Icon:                  {ICON_FILE}")
        print(f"  - Live Reader URL:              {url}")
        print("\n[Server Running] Opening browser... Keep this window open while reading so")
        print("your reading progress & last-read chapter automatically save to reading_progress.json!")
        print("(Press Ctrl+C at any time to stop the server.)")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped local reader server.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert translated Markdown chapters and GLOSSARY.md into the local HTML reader website."
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the local progress-sync server and open the reader website in your default browser.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for --serve (default: 8080).",
    )
    args = parser.parse_args()

    t_count, total_count, g_count = build_site()
    print(
        f"Built reader website in {SITE_DIR}:\n"
        f"  - Translated chapters compiled: {t_count} / {total_count}\n"
        f"  - Glossary entries linked:      {g_count}\n"
        f"  - Entry point:                  {SITE_DIR / 'index.html'}"
    )

    if args.serve:
        serve_site(args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
