# Architectural & Translation Decisions (`DECISIONS.md`)

## 1. Raw HTML Stream Stitching Across `52shuku.net` Pagination Boundaries
- **Context**: `52shuku.net` splits 《飞升从考试开始》 across 568 fixed-byte HTML pages (`bkeSI_2.html` to `bkeSI_569.html`), cutting off raw HTML mid-sentence and mid-`<p>` tag at page boundaries, and starting new chapters mid-page.
- **Decision**: Rather than parsing DOM per page (which would prematurely close split `<p>` tags and break sentences in half), [`scraper.py`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/scraper.py) extracts the raw body slice between `ad_header.js"></script>` and `<div class="pagination2">` from each page, concatenates all 568 slices in order into a single stream, and only then extracts `<p>...</p>` paragraphs and splits by `第X章` headers.
- **Security Hardening**:
  - Enforces a 5 MB cap (`MAX_RESPONSE_BYTES`) on both raw HTTP reads and gzip stream decompression to prevent memory DoS.
  - Unescapes HTML entities and strips HTML tags/angle brackets both before and after entity decoding to prevent XSS/HTML injection in Markdown previewers.
  - Uses a thread-safe rate limiter (`MAX_WORKERS = 4`, `MIN_REQUEST_INTERVAL_SEC = 0.25`) to avoid WAF/IP bans.
  - Protects against duplicate chapter number overwrites via `allocate_unique_filename()`.

## 2. Interactive Antigravity Translation Hub + Auto-Expanding `GLOSSARY.md`
- **Context**: Translating a 132-chapter Xianxia comedy novel requires strict continuity of character names, pronouns, sect terminology, and recurring academic gags while allowing the reader to override any name with an English-sounding alternative at any time.
- **Decision**:
  - [`GLOSSARY.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/GLOSSARY.md) serves as the Single Source of Truth with an explicit `English Name (optional)` override field.
  - Custom project skill [`.agents/skills/novel-translator/SKILL.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/.agents/skills/novel-translator/SKILL.md) combines the target-language context window & 5D audit from `monkey-skills/translation-novel` with the 3-pass anti-translationese restructuring from `novel-translate`.

## 3. Custom Reader Typography & Hover Tooltips
- **Context**: Standard English quote blocks often lose speaker clarity in rapid Chinese polylogues and flatten cultural wordplay.
- **Decision**:
  - **1st-Person Inner Monologue**: `[MONOLOGUE HERE]`
  - **Attributed Dialogue**: New line per speaker starting with `—"Speech here,"-Speaker said, action.`
  - **Unattributed Dialogue**: Wrapped in a Markdown hover tooltip identifying the speaker: `[—"Speech here."](# "Speaker: Character Name")`
  - **Cultural Idioms & Slang**: Adapted into natural English (or kept when iconic) with a Markdown hover tooltip explaining the counterpart: `[adapted phrase](# "Literal CN: ... — meaning")`
