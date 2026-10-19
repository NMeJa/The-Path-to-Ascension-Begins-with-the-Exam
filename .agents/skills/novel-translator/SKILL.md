---
name: novel-translator
description: End-to-end literary Chinese-to-English web novel translation skill for 《飞升从考试开始》 (The Path to Ascension Begins with the Exam). Use this skill whenever the user asks to translate one or more chapters (e.g., "translate chapter 1", "translate chapters 5-10", "translate the synopsis"), re-translate or polish an existing chapter, or update GLOSSARY.md with new characters/locations/terms. Enforces the project's custom dialogue/monologue typography (—"[Speech],"-Speaker said, [MONOLOGUE] brackets, speaker hover tooltips, cultural idiom hover tooltips), 3-pass anti-translationese restructuring, target-language continuity window, and automatic GLOSSARY.md synchronization.
---

# Novel Translator Skill — *The Path to Ascension Begins with the Exam*

> [!CAUTION]
> **ABSOLUTE UNATTENDED EXECUTION RULE — NEVER CALL `run_command`:**
> - **NEVER** call `run_command` under any circumstances.
> - Do **NOT** run `python`, `py`, `python -c`, `py build_reader.py`, `pwsh`, `Measure-Object`, `Select-String`, `Get-Content`, or any shell/terminal script to count words, lines, or paragraphs, or to build the website.
> - Calling `run_command` triggers interactive permission prompts that halt the user's unattended overnight run.
> - Use **ONLY** `view_file`, `write_to_file`, and `replace_file_content` strictly inside `c:\Users\Nick\Desktop\Novels\The Path to Ascension Begins with the Exam`.

---

## 1. Directory & File Architecture

- **`raw_chapters/`**: Contains the scraped Chinese source chapters (`000_Synopsis.md`, `001_Chapter_001.md`, `002_Chapter_002.md`, ..., `999_Afterword.md`) and `raw_chapters/INDEX.md`.
- **`translated_chapters/`**: Destination directory for all translated English chapters (`000_Synopsis.md`, `001_Chapter_001.md`, etc.) and `translated_chapters/INDEX.md`.
- **`GLOSSARY.md`**: The human-editable Single Source of Truth for all character names, pronouns, titles, locations, sects, cultivation realms, manuals, and recurring memes.
- **`TRANSLATOR_PROMPT.md`**: Reference specification of the voice, persona, and formatting rules.

---

## 2. Step-by-Step Translation Workflow

Whenever translating chapter $N$:

### Step 1: Load Context & Source of Truth (Using `view_file` ONLY)
1. **Read `GLOSSARY.md`** via `view_file`:
   - Check every character's `Character Name`, `English Name (optional)`, `Sex` (`he/him` vs `she/her`), `Titles / Epithets`, and `Speech Style & Personality`.
   - **Override Rule**: If `English Name (optional)` or `Location English Name (optional)` is non-empty, you **MUST** use that value instead of the default name.
2. **Read Previous Translated Chapter Tail (Continuity Window)** via `view_file`:
   - If chapter $N - 1$ exists in `translated_chapters/`, view its final 30–40 lines so that scene transitions, cliffhangers, character voices, and tense remain 100% seamless.
3. **Read the Entire Target Raw Chapter** via `view_file`:
   - View `raw_chapters/{N:03d}_Chapter_{N:03d}.md` from line 1 to the end.
   - Translate every single paragraph in full—never skip, condense, or summarize.

---

### Step 2: Execute the 3-Pass Translation

#### Pass 1 — Comprehension & Speaker Resolution
- Identify every character in the scene and map every spoken line (especially untagged Chinese dialogue) to its exact speaker.
- Identify any new named characters, locations, sects, manuals, or special items that do not yet exist in `GLOSSARY.md`.

#### Pass 2 — Idiomatic English Prose & Voice Differentiation
- Translate the narrative into crisp, natural, witty English prose that balances high-Xianxia atmosphere with deadpan academic comedy.
- **Eliminate Translationese**:
  - Avoid stiff connectors (`Moreover`, `Furthermore`, `One had to know that`, `It could be seen that`, `didn't know whether to laugh or cry`).
  - Restructure Chinese topic-comment comma chains into punchy English sentences.
- **Maintain Distinct Character Voices**:
  - **Zhou Hui**: Calm, polite, clinical, terrifyingly patient professor.
  - **Yuwen Xuan**: Boisterous, hot-blooded, dramatic jock who dreads written tests.
  - **Qu Qianxing**: Sharp-tongued, sarcastic, perpetually exhausted administrator.
  - **Ran Deng**: Lazy, unbothered slacker full of random folklore trivia.
  - **Guan Ye**: Quiet, gloomy, earnest, methodical practitioner.
  - **Lu Shulong**: Nervous plant/alchemy nerd who panics outside his specialty.
  - **Mo Bei & Mo Xi**: Lively, mischievous twin teenagers.

#### Pass 3 — Strict Custom Typography & Formatting Enforcement
Apply all formatting rules below without exception:

1. **Direct 1st-Person Inner Monologue (`[MONOLOGUE HERE]`)**:
   - Enclose direct 1st-person inner thoughts in square brackets:
     `[If Master doesn't ascend today, I'm going to die of overwork!]`
   - Keep 3rd-person narrative descriptions of thoughts as normal text.
   - Use single quotes `'...'` for sarcastic in-sentence air-quotes.

2. **Dialogue & Polylogue Turns (`—"Speech here."`)**:
   - Every speaker turn **MUST start on a new line** beginning with `—"`.
   - **With Speaker Tag / Action Beat**: Attach the tag immediately after the closing quote using a hyphen `-` (no spaces around the hyphen before the speaker's name/pronoun):
     `—"No rush,"-Zhou Hui said, shaking his head.`
   - **Pre-Speech Tags in Chinese**: When the Chinese source puts `X道：“...”` before the quote, either flip the tag to the end (`—"...,"-X said.`) or put the setup action in its own narrative paragraph above so the spoken line starts cleanly on a new line with `—"`. (Only keep pre-speech text on the same line if moving it would severely distort the flow/meaning.)
   - **Interrupted / Two-Part Speech by Same Speaker**:
     `—"Listen closely,"-Zhou Hui said, setting down his teacup.-"Anyone who scores below sixty will retake the exam tomorrow."`

3. **Unattributed Dialogue Hover Tooltips (`[—"Speech."](# "Speaker: Name")`)**:
   - Whenever a line of dialogue has **no explicit speaker tag in the sentence**, wrap the entire spoken line in a Markdown link tooltip showing who spoke:
     `[—"No rush."](# "Speaker: Zhou Hui")`

4. **Cultural Adaptation & Idiom Hover Tooltips**:
   - Whenever a Chinese idiom, slang term, or cultural pun (`咸鱼`, `内卷`, `送命题`, `偏科`, `五飞三修`, etc.) is adapted into natural English (or kept as a flavorful literal phrase), wrap the phrase in a Markdown hover tooltip explaining the counterpart:
     `[total slacker](# "Literal CN: salted fish (咸鱼) — slang for someone who has completely given up on ambition")`

5. **Exam Papers, Notices, Manuals & Censorship**:
   - Format in-story **Exam Questions, Notices, and Written Answers** inside Markdown blockquotes (`>`).
   - Format **Cultivation Manuals & Book Titles** in `*Italics*` instead of `《...》`.
   - Uncensor platform self-censorship (`X教` $\rightarrow$ `cult`, `XX` $\rightarrow$ natural English swearing matching the character's tone).

6. **Chapter Navigation Links**:
   - Include clean Markdown navigation links at the very top and bottom of every translated chapter file:
     `[← Previous Chapter](...) | [Table of Contents](INDEX.md) | [Next Chapter →](...)`

---

### Step 3: Save & Sync Glossary (Using File Tools ONLY)
1. **Save Translated File**:
   - Write the completed chapter to `translated_chapters/{N:03d}_Chapter_{N:03d}.md` using `write_to_file`.
2. **Auto-Update `GLOSSARY.md`**:
   - If any new characters, locations, sects, realms, or recurring terms appeared in the chapter, append them to `GLOSSARY.md` using `replace_file_content`.
   - **Do NOT run `build_reader.py` or any other script via `run_command`.**

---

## 3. Chief Editor & Subagent Pipeline (Unattended Overnight Mode)

When the user instructs the main agent to act as the **Chief Editor / Reviewer** and delegate chapter translations to subagents:

### Role A: Main Agent = Chief Editor, Assigner & Quality Reviewer (Gemini 3.8 High)
1. **Zero Terminal Commands**:
   - Neither the Chief Editor nor any Subagent may EVER call `run_command` (no `python -c`, no `py build_reader.py`, no `pwsh`, no `Measure-Object`, no `Select-String`).
2. **Two-Stage Subagent Loop per Chapter (Keeps Main Chat Memory Clean While Reviewing 100% of Every Line)**:
   For each chapter $k$ sequentially:
   - **Stage 1 — Translation Subagent (`Model: "flash"`)**:
     - Invoke a `self` subagent with `Model: "flash"` (Gemini 3.8 Flash Medium/Low) to translate `raw_chapters/{k:03d}_Chapter_{k:03d}.md` 100% unabridged into `translated_chapters/{k:03d}_Chapter_{k:03d}.md` and update `GLOSSARY.md`, using **only** `view_file`, `write_to_file`, and `replace_file_content` (strictly forbidding `run_command`).
   - **Stage 2 — Full-Chapter Editorial Review (`view_file` from Line 1 to End)**:
     - The Chief Editor (or a dedicated `Model: "inherit"` / Gemini 3.8 High Reviewer subagent if translating 15+ chapters in one session) **MUST read the ENTIRE `translated_chapters/{k:03d}_Chapter_{k:03d}.md` from line 1 to the last line** using `view_file` alongside `raw_chapters/{k:03d}_Chapter_{k:03d}.md`.
     - Verify every single section from start to finish:
       - **100% Completeness**: Every paragraph from the Chinese chapter is present; nothing in the middle or end was skipped or summarized.
       - **100% Formatting Accuracy**: Every 1st-person thought uses `[MONOLOGUE]`; every attributed dialogue line starts on a new line with `—"Speech,"-Speaker verb...`; every unattributed dialogue line has `[—"Speech."](# "Speaker: ...")`; cultural idioms have `[phrase](# "Literal CN: ...")` tooltips.
       - **Glossary Accuracy**: Pronouns (`he/him` vs `she/her`), names, and newly added `GLOSSARY.md` entries are accurate.
     - If any issues are found anywhere in the chapter, fix them immediately using `replace_file_content`.
   - **Stage 3 — Advance Immediately**:
     - As soon as Chapter $k$ passes full review, immediately invoke the `Model: "flash"` subagent for Chapter $k+1$ without stopping.
