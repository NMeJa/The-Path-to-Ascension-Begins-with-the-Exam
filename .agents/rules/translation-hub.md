# Workspace Rule: Novel Translation Hub (《飞升从考试开始》)

Whenever working in this repository (`c:\Users\Nick\Desktop\Novels\The Path to Ascension Begins with the Exam`):

1. **ZERO TERMINAL COMMANDS (`run_command` IS STRICTLY FORBIDDEN)**:
   - **NEVER call `run_command`** (do NOT run `pwsh`, `Measure-Object`, `Select-String`, `Get-Content`, `python`, or any shell command) during translation, glossary updates, or editorial review.
   - Calling `run_command` triggers interactive permission prompts that halt unattended/overnight runs.
   - Use **ONLY** native file tools (`view_file`, `write_to_file`, `replace_file_content`). `view_file` already displays `Total Lines` and `Total Bytes` in its output header.
2. **Strict Workspace Boundary**:
   - **NEVER** read, write, or reference any file outside `c:\Users\Nick\Desktop\Novels\The Path to Ascension Begins with the Exam`.
3. **Activate `novel-translator` Skill**:
   - For any request to translate chapters, polish translations, or manage terminology, follow `.agents/skills/novel-translator/SKILL.md` and `TRANSLATOR_PROMPT.md`.
4. **Subagent Model Tier Requirement (Explicit User Preference)**:
   - When the main Reviewer/Editor agent (running on Gemini 3.8 High) invokes subagents via `invoke_subagent` to translate chapters, **ALWAYS** set `Model: "flash"` (Gemini 3.8 Flash Medium) or `Model: "flash_lite"` (Gemini 3.8 Flash Low).
5. **Always Consult & Sync `GLOSSARY.md`**:
   - Read `GLOSSARY.md` before translating.
   - If `English Name (optional)` or `Location English Name (optional)` is filled in by the user, use that English name instead of the default name.
   - Automatically append newly discovered characters, locations, sects, manuals, and recurring terms to `GLOSSARY.md` after translating a chapter.
6. **Strictly Enforce Custom Dialogue & Monologue Formatting**:
   - **Direct 1st-person inner monologue**: `[MONOLOGUE HERE]`
   - **Attributed dialogue (new line per speaker)**: `—"Speech here,"-Speaker said, action.`
   - **Unattributed dialogue (hover tooltip for speaker)**: `[—"Speech here."](# "Speaker: Character Name")`
   - **Culturally adapted idioms/slang (hover tooltip for CN original)**: `[adapted English phrase](# "Literal CN: ... — meaning")`
   - **In-story exam questions & notices**: Markdown blockquotes (`>`)
   - **Book & manual titles**: `*Italics*`
