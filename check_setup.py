"""
check_setup.py: says, in plain words, whether Ask All is ready on this machine, and
what to do about anything that isn't.

    python check_setup.py            full check (about 30 seconds: asks each model "OK?")
    python check_setup.py --no-ping  skip the test questions (installed, but signed in?)

What it checks: Python version, models.toml, each enabled model (installed AND signed
in, by sending it one tiny test question the same way a real run does), which profile
is in use, git (only needed to review a project folder), and the page server.
Read-only: it installs nothing and changes nothing. Exit code 0 = nothing blocking.
"""
import argparse
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Block: how to fix a model that is missing or not signed in, keyed by its name in models.toml
HINTS = {
    "claude": "Install Claude Code:  npm install -g @anthropic-ai/claude-code\n"
              "         then run  claude  once and sign in with your Claude account.",
    "gemini": "Install Antigravity CLI from https://antigravity.google/download\n"
              "         (Windows PowerShell:  irm https://antigravity.google/cli/install.ps1 | iex)\n"
              "         then run  agy  once and sign in with your Google account.",
}
results = []


# Block: one line of the report, remembered so the summary can count failures
def report(level, what, detail="", hint=""):
    results.append(level)
    print(f"[{level:^4}] {what}" + (f": {detail}" if detail else ""))
    if hint:
        print(f"         {hint}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-ping", action="store_true", help="don't send the test question to each model")
    args = ap.parse_args()

    # Block: Python 3.11+ (tomllib, used to read models.toml, arrived in 3.11)
    v = sys.version_info
    if v < (3, 11):
        report("FAIL", "Python", f"{v.major}.{v.minor} found", "Install Python 3.11 or newer from python.org.")
        return 1
    report("OK", "Python", f"{v.major}.{v.minor}.{v.micro}")

    import ask_all                                 # after the version check: it needs tomllib

    # Block: models.toml, and which models are switched on
    try:
        cfg = ask_all.load_config()
    except Exception as e:
        report("FAIL", "models.toml", str(e), "Restore models.toml from the repo.")
        return 1
    models = [m for m in cfg.get("model", []) if m.get("enabled", True)]
    if not models:
        report("FAIL", "Models", "none enabled", "Set enabled = true for at least one model in models.toml.")
    else:
        report("OK", "Models enabled", ", ".join(m["name"] for m in models))

    # Block: each model: installed? then (unless --no-ping) answers a test question?
    workdir = tempfile.mkdtemp(prefix="ask-all-check-")
    for m in models:
        exe = m["command"][0].replace("{PYTHON}", sys.executable)
        hint = HINTS.get(m["name"], "Check the command for this model in models.toml.")
        if not shutil.which(exe):
            report("FAIL", f"{m['name']}", f"'{exe}' is not installed or not on PATH", hint)
            continue
        if args.no_ping:
            report("OK", f"{m['name']}", "installed (not pinged)")
            continue
        r = ask_all.run_model(m, "Reply with exactly: OK", workdir, 180)
        if r["status"] == "ok" and "OK" in r["text"].upper():
            report("OK", f"{m['name']}", f"installed, signed in, answered in {r['secs']}s")
        else:
            first = (r["text"] or "").strip().splitlines()
            report("FAIL", f"{m['name']}", f"{r['status']}: {first[-1][:160] if first else 'no output'}", hint)
    shutil.rmtree(workdir, ignore_errors=True)
    if len([m for m in models]) == 1:
        report("WARN", "Comparison", "only one model enabled, so there is nothing to compare",
               "Enable a second model in models.toml for the agree / disagree view.")

    # Block: which profile the tasks will use
    prof = ask_all.profile_dir()
    if prof.name == "profile.example":
        report("WARN", "Profile", "using profile.example/ (a fictional person)",
               "Copy profile.example to profile and fill in about.md, facts.md, playbook.md, code_rules.md.")
    else:
        missing = [f for f in ("about.md", "facts.md", "playbook.md", "code_rules.md") if not (prof / f).exists()]
        if missing:
            report("FAIL", "Profile", f"{prof} is missing {', '.join(missing)}",
                   "Copy those files from profile.example/ and fill them in.")
        else:
            report("OK", "Profile", str(prof))

    # Block: git (only needed to review a project folder's changes)
    if shutil.which("git"):
        report("OK", "git", "found (code review of a project folder works)")
    else:
        report("WARN", "git", "not found", "Only needed to review a project folder; pasting code works without it.")

    # Block: the page server: running already, or ready to start
    try:
        with urllib.request.urlopen("http://127.0.0.1:8770/api/options", timeout=2) as r:
            report("OK", "Page", "running at http://127.0.0.1:8770")
    except Exception:
        report("OK", "Page", "not running yet",
               "Start it with:  python web/server.py --open   (Windows one-click: scripts/install_windows.ps1)")

    # Block: summary
    fails = results.count("FAIL")
    print()
    print("Ready." if not fails else f"{fails} thing(s) to fix before Ask All will work fully (see above).")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
