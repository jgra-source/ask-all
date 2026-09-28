"""
ask-all: send one input to several AI models and show their answers side by side.

What it does: takes a task (job-post, job-fit, code-review, or any file in tasks/) and
an input (a file, a git diff, or piped text), sends the SAME prompt to every model in
models.toml at once, then has one model list where the answers agree and disagree.
Everything lands in one page: runs/<timestamp>-<task>/report.html.
Follow-ups: `--followup <run>` continues that run as a conversation. Every model gets
the whole thread so far (original request, all answers, earlier follow-ups) plus the
new question; the thread is saved in the run folder and drawn under the first answers.

Why it exists: to get several AI opinions without pasting the same thing into several
chats and comparing them by eye. Used by the chat skill and web/server.py.

Model agnostic: a model is just a command that reads the prompt on stdin and
prints an answer. Adding one is a models.toml edit, never a code change.

Personal details live in a profile folder, never in the task files: profile/ (yours,
kept out of git) or, if that doesn't exist, profile.example/ (a fictional person, so a
fresh copy works on day one). Task files load it with "@include {{PROFILE}}/<file>".
Environment overrides (used by the tests): ASK_ALL_PROFILE, ASK_ALL_CONFIG, ASK_ALL_RUNS.

Usage:
  python ask_all.py job-post --file post.txt
  python ask_all.py job-fit --file jd.txt
  python ask_all.py code-review --diff C:/path/to/repo            (uncommitted changes)
  python ask_all.py code-review --diff C:/path/to/repo --against main
  python ask_all.py code-review --file some_script.py
  python ask_all.py job-post < post.txt
  python ask_all.py --followup 20260928-192529-job-post --file question.txt
  python ask_all.py job-post --file post.txt --dry-run     (show the prompt, call nothing)
Options: --models claude,gemini (default: every enabled model)   --no-compare   --dry-run
Exit code: 0 if at least one model answered, 1 if none did.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = Path(os.environ.get("ASK_ALL_RUNS") or HERE / "runs")
# Stop rather than silently cut: an input this big usually means the wrong diff was picked.
MAX_INPUT_CHARS = 200_000
# An @include may pull in a file that itself has @include lines (a profile file pointing
# at the real fact base elsewhere); this caps the chain so a loop can't run forever.
MAX_INCLUDE_DEPTH = 3


# Block: load models.toml (the list of models and run settings)
def load_config():
    with open(os.environ.get("ASK_ALL_CONFIG") or HERE / "models.toml", "rb") as f:
        return tomllib.load(f)


# Block: which profile folder to use: ASK_ALL_PROFILE, else profile/, else the example.
# Falling back to the example keeps a fresh copy working, and says so out loud so nobody
# mistakes the fictional example person's background for their own.
def profile_dir():
    chosen = os.environ.get("ASK_ALL_PROFILE")
    if chosen:
        return Path(chosen).expanduser().resolve()
    if (HERE / "profile").is_dir():
        return HERE / "profile"
    print("NOTE: using profile.example/ (a fictional person). Copy it to profile/ and "
          "fill in your own details; see README.md.", flush=True)
    return HERE / "profile.example"


# Block: read a tasks/ file with its <!-- notes --> stripped (notes are for humans only)
def read_template(name):
    return re.sub(r"<!--.*?-->\s*", "", (HERE / "tasks" / name).read_text(encoding="utf-8"), flags=re.S)


# Block: turn "@include <path-or-glob>" lines into that file's content.
# {{PROFILE}} in the path is the profile folder. A glob must match exactly ONE file
# (a synced folder can carry a random id in its name, so a pattern finds it, but two
# matches would mean we can't tell which copy is current). Included files may include
# others, up to MAX_INCLUDE_DEPTH deep.
def expand_includes(text, task_path, profile=None, depth=0):
    profile = profile or profile_dir()
    out = []
    for line in text.splitlines():
        m = re.match(r"^@include\s+(.+?)\s*$", line)
        if not m:
            out.append(line)
            continue
        if depth >= MAX_INCLUDE_DEPTH:
            sys.exit(f"ERROR: {task_path.name}: @include nested more than {MAX_INCLUDE_DEPTH} deep")
        pattern = os.path.expanduser(m.group(1).replace("{{PROFILE}}", str(profile)))
        if not os.path.isabs(pattern):
            pattern = str(task_path.parent / pattern)
        hits = sorted(glob.glob(pattern))
        if len(hits) != 1:
            sys.exit(f"ERROR: {task_path.name}: '@include {m.group(1)}' matched "
                     f"{len(hits)} files, needs exactly 1 (looked for {pattern})")
        # included files' <!-- notes --> are for humans too (e.g. _guard.md's header)
        included = Path(hits[0])
        body = re.sub(r"<!--.*?-->\s*", "", included.read_text(encoding="utf-8"), flags=re.S)
        out.append(expand_includes(body, included, profile, depth + 1))
    return "\n".join(out)


# Block: load a task by name (files starting with "_" are internal steps, not tasks)
def load_task(name):
    path = HERE / "tasks" / f"{name}.md"
    if name.startswith("_") or not path.exists():
        names = sorted(p.stem for p in (HERE / "tasks").glob("*.md") if not p.stem.startswith("_"))
        sys.exit(f"ERROR: no task '{name}'. Available: {', '.join(names)}")
    return expand_includes(read_template(path.name), path)


# Block: get the input from --diff, --file or piped stdin, and refuse empty or oversized input.
# raw=True (follow-up questions) skips the "File: name" header a reviewed file gets.
def read_input(args, raw=False):
    if args.diff:
        r = subprocess.run(["git", "-C", args.diff, "diff", args.against], capture_output=True)
        if r.returncode != 0:
            sys.exit(f"ERROR: git diff failed: {r.stderr.decode('utf-8', 'replace').strip()}")
        text = r.stdout.decode("utf-8", "replace")
        # git diff leaves out brand-new files never added to git; warn so a review isn't mistaken for complete
        u = subprocess.run(["git", "-C", args.diff, "ls-files", "--others", "--exclude-standard"],
                           capture_output=True)
        untracked = [f for f in u.stdout.decode("utf-8", "replace").splitlines() if f]
        if untracked:
            print(f"WARNING: {len(untracked)} new file(s) not in git are NOT in this review "
                  f"(e.g. {untracked[0]}). `git add` them to include them.")
        text = f"```diff\n{text}\n```" if text.strip() else ""
    elif args.file:
        p = Path(args.file)
        body = p.read_text(encoding="utf-8")
        text = body if raw else f"File: {p.name}\n\n{body}"
    elif not sys.stdin.isatty():
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
    else:
        sys.exit("ERROR: no input. Use --file, --diff, or pipe text in.")
    if not text.strip():
        sys.exit("ERROR: input is empty, nothing to ask about (no changes in the diff?)")
    if len(text) > MAX_INPUT_CHARS:
        sys.exit(f"ERROR: input is {len(text):,} characters (limit {MAX_INPUT_CHARS:,}). "
                 "Narrow it, e.g. review one file or a smaller diff.")
    return text


# Block: run ONE model: prompt in on stdin, answer out on stdout.
# Every outcome is recorded (ok / failed / timeout / missing) so a model that didn't
# answer shows up as that, never as a silently missing column.
def run_model(model, prompt, workdir, timeout):
    name = model["name"]
    # {PYTHON} = this same Python, so adapters never hit the Microsoft Store "python" stub
    cmd = [c.replace("{HERE}", str(HERE)).replace("{PYTHON}", sys.executable)
           for c in model["command"]]
    exe = shutil.which(cmd[0])
    if not exe:
        return {"name": name, "status": "missing", "secs": 0,
                "text": f"'{cmd[0]}' is not installed or not on PATH."}
    cmd[0] = exe
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True,
                           cwd=workdir, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout", "secs": timeout,
                "text": f"No answer within {timeout}s."}
    secs = round(time.monotonic() - start)
    out = r.stdout.decode("utf-8", "replace").strip()
    err = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not out:
        detail = "\n".join(filter(None, [out, err[-2000:]])) or "(no output)"
        return {"name": name, "status": "failed", "secs": secs,
                "text": f"Exit code {r.returncode}.\n{detail}"}
    return {"name": name, "status": "ok", "secs": secs, "text": out}


# Block: ask several models at once. prompt_for(name) gives each model its prompt
# (the same text for a new run; for follow-ups each is told which answers were its own).
# They run from an empty temp folder OUTSIDE the workspace, so no project instruction
# files (CLAUDE.md, GEMINI.md) get picked up by just one of them.
def ask_models(models, prompt_for, timeout):
    workdir = tempfile.mkdtemp(prefix="ask-all-")
    by_name = {}
    try:
        with ThreadPoolExecutor(max_workers=len(models)) as pool:
            futures = {pool.submit(run_model, m, prompt_for(m["name"]), workdir, timeout): m["name"]
                       for m in models}
            # print each model the moment it finishes: web/server.py reads these lines
            # ("  <name>  <status>  <secs>s") to show live progress on the page
            for fut in as_completed(futures):
                r = fut.result()
                by_name[r["name"]] = r
                print(f"  {r['name']:<12} {r['status']:<8} {r['secs']}s", flush=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return [by_name[m["name"]] for m in models]      # keep models.toml order for the report


# Block: the compare step. Answers are labelled A, B, C (not by model name) so the
# comparing model can't favour its own. Falls back to the first model that answered
# if the configured one isn't available.
def compare(results, all_models, cfg, task, timeout):
    ok = [r for r in results if r["status"] == "ok"]
    if len(ok) < 2:
        return None
    labels = {r["name"]: chr(ord("A") + i) for i, r in enumerate(ok)}
    answers = "\n\n".join(f"### Reviewer {labels[r['name']]}\n{r['text']}" for r in ok)
    prompt = read_template("_compare.md").replace("{{TASK}}", task).replace("{{ANSWERS}}", answers)
    wanted = cfg.get("settings", {}).get("compare_with")
    judge_name = wanted if wanted in labels else ok[0]["name"]
    judge = next(m for m in all_models if m["name"] == judge_name)
    workdir = tempfile.mkdtemp(prefix="ask-all-")
    try:
        result = run_model(judge, prompt, workdir, timeout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    result["labels"] = labels
    return result


# Block: build one model's follow-up prompt: the original request, every answer so far
# (its own marked "(you)"), the comparison, earlier follow-ups, then the new question
def followup_prompt(run_dir, state, thread, question, me):
    def answers(results):
        out = []
        for r in results:
            mark = " (you)" if r["name"] == me else ""
            body = r["text"] if r["status"] == "ok" else f"({r['name']} did not answer this one)"
            out += [f"#### {r['name']}{mark}", body, ""]
        return out

    parts = [(run_dir / "prompt.md").read_text(encoding="utf-8"), "", "---",
             "## The conversation so far", "", "### First answers", ""]
    parts += answers(state["results"])
    comp = state.get("comparison")
    if comp and comp.get("status") == "ok":
        key = ", ".join(f"{letter} = {name}" for name, letter in comp["labels"].items())
        parts += [f"#### Comparison of the first answers ({key})", comp["text"], ""]
    for i, turn in enumerate(thread, 1):
        parts += [f"### Follow-up {i}: the user asked", turn["question"], ""]
        parts += answers(turn["results"])
    parts += ["---", "## The user's new follow-up question", question, "",
              read_template("_followup.md").replace("{{ME}}", me)]
    return "\n".join(parts)


# Block: write the one-page report: comparison on top, one column per model, then
# each follow-up (question, then the answers) in order
def write_report(path, task, results, comparison, source, thread=()):
    esc = html.escape
    badge = {"ok": "answered", "failed": "FAILED", "timeout": "TIMED OUT", "missing": "NOT INSTALLED"}

    def cards(rs):
        return "".join(
            f'<section class="card {r["status"]}"><h2>{esc(r["name"])} '
            f'<span class="badge">{badge[r["status"]]} · {r["secs"]}s</span></h2>'
            f'<div class="body">{esc(r["text"])}</div></section>'
            for r in rs)

    if comparison:
        key = ", ".join(f"{letter} = {name}" for name, letter in comparison["labels"].items())
        head = (f'<section class="card compare {comparison["status"]}"><h2>Where they agree and disagree '
                f'<span class="badge">written by {esc(comparison["name"])} · {key}</span></h2>'
                f'<div class="body">{esc(comparison["text"])}</div></section>')
    else:
        head = ('<section class="card compare"><h2>No comparison</h2><div class="body">'
                'Fewer than two models answered, or --no-compare was used.</div></section>')
    turns = "".join(
        f'<h3 class="turn">Follow-up {i} · {esc(t.get("asked", ""))}</h3>'
        f'<section class="card question"><div class="body">{esc(t["question"])}</div></section>'
        f'<div class="grid">{cards(t["results"])}</div>'
        for i, t in enumerate(thread, 1))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ask-all · {esc(task)}</title>
<style>
/* Block: colour tokens, light and dark */
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e2e2dc; --bad:#b3261e; --accent:#2f5d8a; --q:#eef3f8; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --card:#1f1f1d; --ink:#ececea; --muted:#9a9a94; --line:#333330; --bad:#f2b8b5; --accent:#8fb8e0; --q:#1c2631; }} }}
/* Block: page layout; columns stack on a phone */
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:1400px; margin:0 auto; padding:16px; }}
header p {{ color:var(--muted); margin:4px 0 16px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(340px, 1fr)); gap:16px; }}
/* Block: answer cards, follow-up questions and status badges */
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin-bottom:16px; min-width:0; }}
.card.compare {{ border-left:4px solid var(--accent); }}
.card.question {{ background:var(--q); }}
.card.failed, .card.timeout, .card.missing {{ border-left:4px solid var(--bad); }}
h1 {{ font-size:20px; margin:0; }}
h2 {{ font-size:16px; margin:0 0 10px; }}
h3.turn {{ font-size:15px; margin:24px 0 8px; color:var(--muted); }}
.badge {{ font-size:12px; font-weight:400; color:var(--muted); margin-left:6px; }}
.body {{ white-space:pre-wrap; overflow-wrap:anywhere; }}
</style></head>
<body><main>
<!-- Section: what was asked -->
<header><h1>ask-all · {esc(task)}</h1><p>{esc(source)}</p></header>
<!-- Section: comparison summary -->
{head}
<!-- Section: each model's full first answer -->
<div class="grid">{cards(results)}</div>
<!-- Section: follow-up conversation, oldest first -->
{turns}
</main></body></html>"""
    path.write_text(page, encoding="utf-8")


# Block: pick which models to ask: --models if given, else every enabled one
def pick_models(args, all_models, pool=None):
    known = {m["name"] for m in all_models}
    if args.models:
        wanted = [n.strip() for n in args.models.split(",") if n.strip()]
        unknown = [n for n in wanted if n not in known]
        if unknown:
            sys.exit(f"ERROR: unknown model(s) {unknown}; see models.toml")
    else:
        wanted = [m["name"] for m in all_models if m.get("enabled", True)]
    if pool is not None:                 # follow-ups: only models that were in the original run
        wanted = [n for n in wanted if n in pool]
    models = [m for m in all_models if m["name"] in wanted]
    if not models:
        sys.exit("ERROR: no models selected")
    return models


# Block: a new run: build the prompt, ask everyone, compare, save state and report
def new_run(args, cfg, all_models, timeout):
    models = pick_models(args, all_models)
    task_text = load_task(args.task)
    user_input = read_input(args)
    prompt = (task_text.replace("{{INPUT}}", user_input) if "{{INPUT}}" in task_text
              else f"{task_text}\n\n{user_input}")
    # --dry-run: show exactly what every model would receive, call nothing, save nothing
    if args.dry_run:
        sys.stdout.buffer.write(prompt.encode("utf-8"))
        print(f"\n\n--- dry run: {len(prompt):,} characters would go to "
              f"{', '.join(m['name'] for m in models)}; nothing was sent.")
        sys.exit(0)
    stamp = datetime.now()
    source = (f"diff of {args.diff} vs {args.against}" if args.diff
              else f"file {args.file}" if args.file else "pasted input")
    source = f"{source} · {stamp:%Y-%m-%d %H:%M}"

    # Save the exact prompt so any answer can be traced back to what was asked
    run_dir = RUNS / f"{stamp:%Y%m%d-%H%M%S}-{args.task}"
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "input.txt").write_text(user_input, encoding="utf-8")   # the page shows its first line as the title

    print(f"Asking {', '.join(m['name'] for m in models)} at once (usually 1-3 minutes)...", flush=True)
    results = ask_models(models, lambda _name: prompt, timeout)
    for r in results:
        (run_dir / f"{r['name']}.md").write_text(r["text"], encoding="utf-8")

    comparison = None
    if not args.no_compare:
        print("Comparing answers...", flush=True)
        comparison = compare(results, all_models, cfg, args.task, timeout)
        if comparison:
            (run_dir / "compare.md").write_text(comparison["text"], encoding="utf-8")
            print(f"  compare by {comparison['name']}: {comparison['status']} {comparison['secs']}s")

    # results.json is what lets this run be continued with follow-ups later
    state = {"task": args.task, "source": source, "results": results, "comparison": comparison}
    (run_dir / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    write_report(run_dir / "report.html", args.task, results, comparison, source)
    return run_dir, results


# Block: a follow-up on an existing run: whole thread in, new answers appended to thread.json
def followup_run(args, all_models, timeout):
    run_dir = Path(args.followup)
    if not run_dir.is_absolute():
        run_dir = RUNS / args.followup
    state_file = run_dir / "results.json"
    if not state_file.exists():
        sys.exit(f"ERROR: {run_dir.name} can't take follow-ups: it has no results.json "
                 "(it was made before follow-ups existed). Start a new run instead.")
    if args.diff:
        sys.exit("ERROR: a follow-up is a question; use --file or pipe it in, not --diff")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    thread_file = run_dir / "thread.json"
    thread = json.loads(thread_file.read_text(encoding="utf-8")) if thread_file.exists() else []
    question = read_input(args, raw=True).strip()
    models = pick_models(args, all_models, pool={r["name"] for r in state["results"]})
    if args.dry_run:                     # show the first model's prompt; nothing is sent
        sys.stdout.buffer.write(followup_prompt(run_dir, state, thread, question, models[0]["name"]).encode("utf-8"))
        print("\n\n--- dry run: nothing was sent.")
        sys.exit(0)

    print(f"Follow-up {len(thread) + 1}: asking {', '.join(m['name'] for m in models)}...", flush=True)
    results = ask_models(models, lambda name: followup_prompt(run_dir, state, thread, question, name), timeout)
    thread.append({"question": question, "asked": f"{datetime.now():%Y-%m-%d %H:%M}", "results": results})
    thread_file.write_text(json.dumps(thread, indent=2), encoding="utf-8")
    write_report(run_dir / "report.html", state["task"], state["results"], state["comparison"],
                 state["source"], thread)
    return run_dir, results


# Block: entry point: parse options, then either start a new run or continue one
def main():
    ap = argparse.ArgumentParser(description="Ask several AI models the same thing, side by side.")
    ap.add_argument("task", nargs="?", help="task name: a file in tasks/ (e.g. job-post, job-fit, code-review)")
    ap.add_argument("--file", help="read the input (or the follow-up question) from this file")
    ap.add_argument("--diff", metavar="REPO", help="review the git diff of this repo")
    ap.add_argument("--against", default="HEAD", help="with --diff: compare to this ref (default HEAD = uncommitted changes)")
    ap.add_argument("--models", help="comma-separated model names (default: every enabled model)")
    ap.add_argument("--no-compare", action="store_true", help="skip the agree/disagree summary")
    ap.add_argument("--followup", metavar="RUN", help="continue this run (folder name in runs/) with a follow-up question")
    ap.add_argument("--dry-run", action="store_true", help="print the exact prompt the models would get, and send nothing")
    args = ap.parse_args()
    if bool(args.task) == bool(args.followup):
        ap.error("give a task for a new run, OR --followup RUN to continue one")

    cfg = load_config()
    all_models = cfg.get("model", [])
    timeout = int(cfg.get("settings", {}).get("timeout_seconds", 600))
    if args.followup:
        run_dir, results = followup_run(args, all_models, timeout)
    else:
        run_dir, results = new_run(args, cfg, all_models, timeout)

    # Say where the page is (web/server.py reads this exact line)
    answered = sum(r["status"] == "ok" for r in results)
    print(f"Done: {answered}/{len(results)} models answered. Report: {run_dir / 'report.html'}")
    sys.exit(0 if answered else 1)


if __name__ == "__main__":
    main()
