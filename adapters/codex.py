"""
Adapter: lets OpenAI's Codex CLI meet ask-all's model contract: prompt in on stdin as
plain text, answer out on stdout, non-zero exit on failure.

STATUS: UNTESTED against a real Codex install. Written 2026-09-28 from Codex's source
(openai/codex, codex-rs/exec/src/cli.rs and exec_events.rs, CLI 0.158.0) because the
owner chose not to install Codex yet. The event parsing is covered by unit tests on
sample events in that documented format (tests/test_codex_adapter.py). The first real
run should be checked by hand; see README "Codex".

Why an adapter at all: `codex exec -` alone would meet the contract (stdin in, final
message on stdout), but Codex can run commands and read files even in its read-only
sandbox. This asks for its JSON event stream instead, so every command, file change,
tool call or web search it makes is seen and reported at the top of the answer, never
hidden. The switches:
  --ignore-user-config      don't load ~/.codex/config.toml: none of the user's MCP
                            servers (connected apps) come along; sign-in still works
  --ignore-rules            don't load the user's command-approval rules
  --sandbox read-only       no file writes (reads and commands can still happen: flagged)
  -c web_search=disabled    no web search (bare value: Codex reads it as a string)
  --ephemeral               don't save the session to disk
  --skip-git-repo-check     ask-all runs models from an empty temp folder, not a repo
  --json                    one JSON event per line on stdout

Usage (from models.toml): {PYTHON} {HERE}/adapters/codex.py [--model NAME]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# item types that mean Codex did something rather than only answered
ACTION_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "collab_tool_call", "web_search")


# Block: the command line; every safety switch is always on
def build_command(codex, model=None):
    cmd = [codex, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
           "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
           "-c", "web_search=disabled"]
    if model:
        cmd += ["--model", model]
    return cmd + ["-"]                   # "-": read the prompt from stdin


# Block: read Codex's JSON events into (answer, actions it took, error).
# The answer is the LAST agent_message (earlier ones can be interim notes). An action is
# any command, file change, tool call or search; a command Codex asked for but that was
# declined still counts, reported as declined, so the page shows it was attempted.
def parse_events(lines):
    answer, actions, error = "", [], None
    for raw in lines:
        try:
            ev = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue                     # progress noise, if any, is not JSON
        kind = ev.get("type")
        if kind == "item.completed":
            item = ev.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message" and (item.get("text") or "").strip():
                answer = item["text"].strip()
            elif itype in ACTION_ITEMS:
                what = item.get("command") or item.get("query") or item.get("tool") or \
                    ", ".join(c.get("path", "?") for c in item.get("changes") or []) or itype
                status = item.get("status")
                actions.append(f"{itype}({what})" + (f" [{status}]" if status and status != "completed" else ""))
        elif kind == "turn.failed":
            error = ((ev.get("error") or {}).get("message")) or "turn failed"
        elif kind == "error":
            error = ev.get("message") or "error"
    return answer, actions, error


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Codex model id (default: Codex's own default)")
    args = ap.parse_args()

    # Block: read the plain-text prompt; find the codex command
    prompt = sys.stdin.buffer.read().decode("utf-8", "replace")
    if not prompt.strip():
        sys.exit("empty prompt on stdin")
    codex = shutil.which("codex")
    if not codex:
        sys.exit("codex (OpenAI Codex CLI) not found on PATH. Install: npm install -g @openai/codex, "
                 "then run `codex` once and sign in.")

    # Block: run it, prompt on stdin, events on stdout
    r = subprocess.run(build_command(codex, args.model), input=prompt.encode("utf-8"),
                       capture_output=True, creationflags=NO_WINDOW)
    answer, actions, error = parse_events(r.stdout.decode("utf-8", "replace").splitlines())

    # Block: no answer = failure, reported with Codex's own reason
    if not answer:
        err = error or r.stderr.decode("utf-8", "replace").strip()[-1500:] or f"exit {r.returncode}"
        sys.exit(f"codex gave no answer: {err}")

    # Block: flag anything Codex did beyond answering, on top of the answer
    if actions:
        answer = ("WARNING: Codex used tools during this answer, against its instructions: "
                  + "; ".join(actions) + ". Check whether the input told it to.\n\n" + answer)
    sys.stdout.buffer.write(answer.encode("utf-8"))


if __name__ == "__main__":
    main()
