# 📚 *The Path to Ascension Begins with the Exam* (《飞升从考试开始》) — Translation Hub

An end-to-end scraping, glossary management, AI literary translation, and local HTML reader workspace for **《飞升从考试开始》** by **雾入 (Wu Ru)**.

---

## 📁 Project Structure

- [`Start_Reader.bat`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/Start_Reader.bat) — One-click Windows launcher. Compiles `GLOSSARY.md` + `translated_chapters/*.md`, generates `reader_icon.ico` + `Launch Novel Reader.lnk` (with the custom Immortal Jade Scroll icon), starts the local progress server, and opens the reader in your browser.
- [`reading_progress.json`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/reading_progress.json) — Local JSON database that automatically remembers your **last read chapter** (`last_read_code`), **all chapters you have already read** (`read_chapters`), **per-chapter scroll positions** (`scroll_positions`), and **reader appearance settings** (`settings`).
- [`build_reader.py`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/build_reader.py) — Zero-dependency Markdown-to-HTML compiler and local JSON DB sync server.
- [`scraper.py`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/scraper.py) — Zero-dependency Python 3 script that downloads all 568 pages from `52shuku.net`, caches raw pages in `.cache/pages/`, stitches split paragraphs across page boundaries, sanitizes HTML/watermarks, and outputs 260 clean chapter files into `raw_chapters/`.
- [`GLOSSARY.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/GLOSSARY.md) — Human-editable **Single Source of Truth** for Characters, Locations, Sects, Cultivation Realms, Manuals, and Exam Slang. Fill in `English Name (optional)` on any entry to override the default Pinyin/English name.
- [`TRANSLATOR_PROMPT.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/TRANSLATOR_PROMPT.md) — Standalone Master Prompt for Gemini 3.8 Flash / Antigravity with complete voice, typography, and hover-tooltip specifications.
- [`.agents/skills/novel-translator/SKILL.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/.agents/skills/novel-translator/SKILL.md) — Custom project skill that powers interactive chapter translation, continuity checking, and automatic glossary expansion.
- [`.agents/rules/translation-hub.md`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/.agents/rules/translation-hub.md) — Workspace rule ensuring Antigravity always enforces your custom formatting and glossary rules.

---

## 🚀 Quick Start

### 1. Launch the Local Reader Website
Double-click **[`Start_Reader.bat`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/Start_Reader.bat)** (on first run, it also creates **`Launch Novel Reader.lnk`** with a custom Immortal Jade Scroll icon that you can pin or move to your Desktop!).
- It automatically parses `GLOSSARY.md` and `translated_chapters/*.md`, opens the reader in your browser, and resumes at your last-read chapter and scroll position.
- Every chapter you open (or mark as read/unread) and every setting you change is saved directly to [`reading_progress.json`](file:///c:/Users/Nick/Desktop/Novels/The%20Path%20to%20Ascension%20Begins%20with%20the%20Exam/reading_progress.json).
- While the reader is open, if you translate new chapters in Antigravity, simply click the **🔄 Sync Files** button in the top-right bar of the website to compile and load the new chapters immediately!

### 2. Translate Chapters in Antigravity
Simply ask in chat:
- *"Translate Chapter 1"*
- *"Translate Chapters 1 to 3"*
