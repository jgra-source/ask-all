---
name: ask-all
description: Get side-by-side opinions from several AI models (Claude, Gemini, or any CLI listed in ask-all's models.toml) on the same input, plus a list of where they agree and disagree. Use when the user says "ask all", "second opinion", "what does Gemini think", "get both opinions", or "compare Claude and Gemini on this", on a pasted job post, job description, or a code change.
---

<!--
Optional: lets you use Ask All from a Claude Code chat instead of the web page.
INSTALL: copy this folder to ~/.claude/skills/ask-all/ and replace ASK_ALL_DIR below
with the full path to your ask-all folder (for example /home/you/ask-all or
C:/Users/you/ask-all). If "python" on your machine is not Python 3.11+, use the full
path to one that is (Windows: often ...\AppData\Local\Programs\Python\Python312\python.exe).
-->

# ask-all

Runs `ask_all.py`, which sends the same prompt to every enabled model in `models.toml`
at once and writes one page (`report.html`) with the comparison on top and each model's
full answer below. The web page (`python ASK_ALL_DIR/web/server.py --open`) shows the
same runs.

## Steps

1. **Pick the task.**
   - Freelance job post or brief → `job-post`.
   - Full-time or long-term contract role → `job-fit`.
   - Code change → `code-review --diff <repo path>` (add `--against main` for a whole
     branch); a single file → `code-review --file <path>`.
   - A follow-up on an earlier result → `--followup <run folder name> --file <question file>`.
2. **Save pasted text** exactly as given to a temporary file.
3. **Run it** (allow up to 10 minutes):
   ```
   python ASK_ALL_DIR/ask_all.py <task> --file <path>
   ```
4. **Show the result:** the last line prints the `report.html` path. Open or send it, or
   point the user at the web page, where the run appears in the history.
5. **Reply briefly:** the bottom line, the biggest disagreement, and any model that did
   NOT answer, with its reason.

## Rules

- A model marked FAILED, TIMED OUT or NOT INSTALLED didn't answer. Say so plainly and
  never fill its column in with your own opinion.
- Don't add your own verdict unless asked: you are one of the models, so your extra
  opinion would give that model two votes.
- Setup problems: run `python ASK_ALL_DIR/check_setup.py`; it names the fix.
