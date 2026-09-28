"""
Adapter: lets Antigravity CLI (agy) meet ask-all's model contract — prompt in on
stdin as plain text, answer out on stdout, non-zero exit on failure.

Why it's needed: agy ignores plain text piped into it, and a job-post prompt
(~42k chars) is too long to pass as a command-line argument on Windows (~32k cap).
agy DOES read piped input in its stream-json mode, so this wraps the prompt in the
one-line JSON message it expects and pulls the answer out of its JSON result.
Message format found by testing agy 1.2.12 on 2026-09-28:
  in:  {"event":"user","message":{"content":"<prompt>"}}
  out: {"event":"result","result":{"status":"SUCCESS","response":"<answer>", ...}}

Usage (from models.toml): {PYTHON} {HERE}/adapters/agy.py [--model NAME]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# agy stops itself a little before ask-all's own 600s limit, so it doesn't linger
PRINT_TIMEOUT = "580s"
# agy's built-in auto-updater (runs at most every 15 min) starts a separate background
# process that flashes a console window on screen: it was the "cmd pop-up" seen on Windows
# (its log times 20:08 and 20:37 matched the pop-up reports, 2026-09-28). So ask-all turns it off
# for its own runs and instead runs `agy update` itself, which updates in place, hidden,
# at most once a day. Without the daily update agy would drift out of date and could be
# refused by Google, the way Gemini CLI was.
# The value must be "true": "1" is ignored (agy still spawned its updater at 20:53 on
# 2026-09-28, and the Windows Terminal pop-up followed 2s later); with "true" agy logs
# "Auto-update disabled via environment variable" and spawns nothing (verified 23:22).
NO_AUTO_UPDATE = {**os.environ, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}
UPDATE_STAMP = Path(__file__).with_name(".agy-last-update")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# Block: once a day, update agy quietly; a failed update never blocks the answer
def daily_update(agy):
    try:
        if UPDATE_STAMP.exists() and time.time() - UPDATE_STAMP.stat().st_mtime < 86400:
            return
        subprocess.run([agy, "update"], capture_output=True, timeout=180, creationflags=NO_WINDOW)
        UPDATE_STAMP.touch()
    except (OSError, subprocess.SubprocessError):
        pass


# Block: find agy.exe — PATH first, then the installer's default folder (a shell
# opened before the install won't have the new PATH yet)
def find_agy():
    found = shutil.which("agy")
    if found:
        return found
    default = os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
    if os.path.exists(default):
        return default
    sys.exit("agy (Antigravity CLI) not found on PATH or in %LOCALAPPDATA%\\agy\\bin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="agy model id, see `agy models`")
    args = ap.parse_args()

    # Block: read the plain-text prompt and wrap it as agy's one-line JSON message
    prompt = sys.stdin.buffer.read().decode("utf-8", "replace")
    if not prompt.strip():
        sys.exit("empty prompt on stdin")
    line = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"

    # Block: run agy headless. --mode plan = read-only, it can't change files.
    # --print= (empty) turns on print mode; the prompt comes from stdin instead.
    agy = find_agy()
    daily_update(agy)
    cmd = [agy, "--mode", "plan", "--input-format", "stream-json",
           "--output-format", "stream-json", "--print-timeout", PRINT_TIMEOUT]
    if args.model:
        cmd += ["--model", args.model]
    cmd.append("--print=")
    r = subprocess.run(cmd, input=line.encode("utf-8"), capture_output=True,
                       env=NO_AUTO_UPDATE, creationflags=NO_WINDOW)

    # Block: read agy's events: the final "result", plus every tool it actually ran.
    # The prompt forbids tools, but agy CAN open web pages with no prompt in headless
    # mode, so any tool that ran is reported at the top of the answer, never hidden.
    result = None
    tools_used = []
    for raw in r.stdout.decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "result":
            result = ev.get("result", {})
        step = ev.get("step_update") or {}
        if step.get("step_type") == "tool" and step.get("state") == "DONE":
            info = step.get("tool_info") or {}
            params = ", ".join(str(v) for v in (info.get("parameters") or {}).values())
            tools_used.append(f"{step.get('tool_name')}({params})")
    if not result:
        err = r.stderr.decode("utf-8", "replace").strip()[-1500:]
        sys.exit(f"agy gave no result (exit {r.returncode}). {err}")
    answer = (result.get("response") or "").strip()
    if result.get("status") != "SUCCESS" or not answer:
        # a tool the model tried (e.g. reading a file) was refused, which leaves the answer
        # blank; name it, so a FAILED column says why instead of just "empty answer"
        denied = ", ".join(d.get("display_name") or d.get("action", "?")
                           for d in result.get("denied_actions") or [])
        why = result.get("error") or (f"it tried a blocked tool ({denied}) and stopped" if denied
                                      else "empty answer")
        sys.exit(f"agy status {result.get('status')}: {why}")

    # Block: flag any tool use on top of the answer
    if tools_used:
        answer = ("WARNING: Gemini used tools during this answer, against its instructions: "
                  + "; ".join(tools_used) + ". Check whether the input told it to.\n\n" + answer)

    # Block: print the answer as UTF-8 regardless of the console's code page
    sys.stdout.buffer.write(answer.encode("utf-8"))


if __name__ == "__main__":
    main()
