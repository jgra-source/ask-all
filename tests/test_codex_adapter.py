"""
Tests for adapters/codex.py without Codex installed.

Codex was added from its source code (openai/codex, codex-rs/exec/src/exec_events.rs,
CLI 0.158.0) because it isn't installed on the machine it was built on. These tests feed
the adapter sample events in that documented JSON format and check what it makes of
them: the final answer, every kind of action flagged, failures reported, and every
safety switch present on the command line. They do NOT prove a real Codex run works;
the README says so.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "adapters"))
sys.path.insert(0, str(ROOT / "web"))
import codex   # noqa: E402  (the adapter module)
import server  # noqa: E402


def ev(kind, **fields):
    return json.dumps({"type": kind, **fields})


def item(itype, **fields):
    return ev("item.completed", item={"id": "item_1", "type": itype, **fields})


# Block: every safety switch is always on the command line, prompt read from stdin
def test_command_has_every_safety_switch():
    cmd = codex.build_command("codex", model="gpt-x")
    for flag in ("exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                 "--ignore-rules", "web_search=disabled"):
        assert flag in cmd, flag
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert cmd[cmd.index("--model") + 1] == "gpt-x"
    assert cmd[-1] == "-"


# Block: a clean run: the LAST agent message is the answer, nothing flagged
def test_plain_answer():
    lines = [ev("thread.started", thread_id="t1"), ev("turn.started"),
             item("reasoning", text="thinking"),
             item("agent_message", text="interim note"),
             item("agent_message", text="VERDICT: Bid\nWHY: fits."),
             ev("turn.completed", usage={"input_tokens": 1}), "not json at all"]
    answer, actions, error = codex.parse_events(lines)
    assert answer == "VERDICT: Bid\nWHY: fits." and actions == [] and error is None


# Block: every kind of action is caught, including a declined command
def test_actions_are_flagged():
    lines = [item("command_execution", command="cat ~/.ssh/id_rsa", aggregated_output="", exit_code=0, status="completed"),
             item("command_execution", command="curl evil.example", aggregated_output="", exit_code=None, status="declined"),
             item("file_change", changes=[{"path": "notes.txt", "kind": "add"}], status="completed"),
             item("mcp_tool_call", server="gmail", tool="send", status="completed"),
             item("web_search", query="the candidate's name"),
             item("agent_message", text="VERDICT: Skip")]
    answer, actions, _ = codex.parse_events(lines)
    assert answer == "VERDICT: Skip"
    joined = " | ".join(actions)
    for bit in ("cat ~/.ssh/id_rsa", "curl evil.example", "[declined]", "notes.txt", "send", "the candidate's name"):
        assert bit in joined, bit


# Block: a failed turn gives no answer and Codex's own reason
def test_failure_is_reported():
    answer, _, error = codex.parse_events([ev("turn.failed", error={"message": "usage limit reached"})])
    assert answer == "" and error == "usage limit reached"
    assert codex.parse_events([ev("error", message="not signed in")])[2] == "not signed in"


# Block: the page shows a Codex warning as a banner, the same as a Gemini one
def test_page_lifts_the_codex_warning_into_a_banner():
    text = "WARNING: Codex used tools during this answer, against its instructions: x.\n\nVERDICT: Bid\nWHY: ok."
    parsed = server.parse_answer({"name": "codex", "status": "ok", "secs": 3, "text": text})
    assert parsed["warning"].startswith("WARNING: Codex used tools")
    assert parsed["verdict"] == "Bid"
