"""
ask-all web: a local page for asking several AI models the same thing at once.

What it does: serves a page at http://127.0.0.1:8770 where you paste a job post or
code, picks one of three tasks, and presses Ask. Behind the page it runs the SAME
ask_all.py the chat uses (Claude headless + Gemini via Antigravity). It then reads the
run's saved answers and hands the page tidy pieces (each model's VERDICT and WHY line,
its answer split into sections, the comparison split into Agree / Disagree / Only one),
so the page can show a glanceable verdict board instead of walls of text.
Follow-up questions run `ask_all.py --followup <run>`; the thread lives in the run folder.
Why a server: a web page alone can't start programs on the PC; this tiny server does.

Safety: listens on 127.0.0.1 only; refuses requests carrying another website's Origin
or Host; JSON-only POSTs; only the three page tasks; only files it knows by name.

Start: `pythonw server.py --open` (the desktop shortcut): starts the server if it isn't
already running, then opens the page. No console window. Stdlib only, no installs.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent                       # the ask-all folder
RUNS = ROOT / "runs"
HOST, PORT = "127.0.0.1", 8770          # 8765/8766 are taken by other preview configs
URL = f"http://{HOST}:{PORT}/"
OK_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
OK_ORIGINS = {f"http://{h}" for h in OK_HOSTS}
RUN_NAME = re.compile(r"^\d{8}-\d{6}-[\w-]+$")   # the folder names ask_all.py creates
# The page offers ONLY these tasks (owner's decision, 2026-09-28: "limit the answers to those three").
# A new file dropped in tasks/ stays CLI-only until it is added here on purpose.
PAGE_TASKS = ("job-post", "job-fit", "code-review")
# Files the page may load, by exact name: nothing else on disk is reachable
STATIC = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
          "/app.js": ("app.js", "text/javascript")}
FONT_NAME = re.compile(r"^[A-Za-z-]+\.ttf$")
LOG = WEB / "server.log"
# pythonw has no console; its child python must not pop one up either
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# run ask_all with python.exe (not pythonw) so its printed progress can be captured
PYTHON = str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable

jobs = {}                 # job id -> status dict, kept in memory while the server runs
busy_runs = set()         # runs with a follow-up in flight: two at once would overwrite each other's thread
jobs_lock = threading.Lock()


# Block: log to a file; under pythonw there is no console for stderr
def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


# Block: what the page can offer: the allowed tasks that exist in tasks/, enabled models
def options():
    tasks = [t for t in PAGE_TASKS if (ROOT / "tasks" / f"{t}.md").exists()]
    with open(ROOT / "models.toml", "rb") as f:
        cfg = tomllib.load(f)
    models = [m["name"] for m in cfg.get("model", []) if m.get("enabled", True)]
    return {"tasks": tasks, "models": models}


# ---------------------------------------------------------------------------
# Reading answers into pieces the page can line up
# ---------------------------------------------------------------------------

LABEL_LINE = re.compile(r"^[\s>*_`#-]*(VERDICT|WHY|AGREEMENT)[\s*_`]*:[\s*_`]*(.+?)[\s*_`]*$", re.I | re.M)
HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*#*\s*$", re.M)
SUSPICIOUS = "suspicious instructions in the input"


# Block: pull "VERDICT: x" / "WHY: y" / "AGREEMENT: z" lines out of an answer
def labels(text):
    found = {}
    for m in LABEL_LINE.finditer(text or ""):
        key = m.group(1).lower()
        if key not in found:
            # "Bid | Bid with care | Skip" copied verbatim means the model didn't pick one
            value = m.group(2).strip().rstrip(".")
            found[key] = None if "|" in value else value
    return found


# Block: split an answer on its "## Heading" lines. The text before the first heading
# (minus the label lines) is kept as "preamble". No headings at all (older runs, or a
# model ignoring the format) = one "Answer" section, so nothing is ever dropped.
def sections(text):
    text = text or ""
    heads = list(HEADING.finditer(text))
    pre = LABEL_LINE.sub("", text[:heads[0].start()] if heads else "").strip()
    if not heads:
        return pre, ([{"title": "Answer", "body": pre}] if pre else [])
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        title = re.sub(r"^[\d.)\s]+", "", h.group(1)).strip(" *_:")
        out.append({"title": title, "body": text[h.end():end].strip()})
    return pre, out


# Block: one model's answer as pieces: verdict, why, tool warning, sections, suspicious text
def parse_answer(r):
    text = r.get("text") or ""
    warning = None
    if re.match(r"WARNING: \w+ used tools", text):              # set by adapters/agy.py and codex.py
        warning, _, text = text.partition("\n\n")
    lab = labels(text) if r.get("status") == "ok" else {}
    pre, secs = sections(text) if r.get("status") == "ok" else ("", [])
    suspicious = next((s["body"] for s in secs if s["title"].lower() == SUSPICIOUS), None)
    secs = [s for s in secs if s["title"].lower() != SUSPICIOUS]
    return {"name": r["name"], "status": r["status"], "secs": r.get("secs", 0),
            "verdict": lab.get("verdict"), "why": lab.get("why"), "warning": warning,
            "preamble": pre, "sections": secs, "suspicious": suspicious,
            "text": text if r.get("status") != "ok" else None}   # full text only for failures


# Block: the comparison as pieces; "Reviewer A" / "A:" are mapped back to model names
def parse_compare(comp):
    if not comp:
        return None
    text = comp.get("text") or ""
    names = {letter: name.capitalize() for name, letter in (comp.get("labels") or {}).items()}
    for letter, name in names.items():
        text = re.sub(rf"\bReviewer {letter}\b", name, text)
        text = re.sub(rf"(^|[\s(;*-]){letter}(?=:| says| said| flags| also| only|'s|’s)", rf"\1{name}", text, flags=re.M)
    _, secs = sections(text)
    by = {s["title"].lower(): s["body"] for s in secs}
    pick = lambda *keys: next((by[k] for k in by if any(x in k for x in keys)), "")
    return {"status": comp.get("status"), "by": comp.get("name"),
            "agreement": labels(text).get("agreement"),
            "bottom": pick("bottom"), "agree": pick("agree on"),
            "disagree": pick("disagree"), "only": pick("only one")}


# Block: a short title for a run: the first meaningful line of what was pasted
def run_title(run_dir):
    text = ""
    if (run_dir / "input.txt").exists():
        text = (run_dir / "input.txt").read_text(encoding="utf-8", errors="replace")
    elif (run_dir / "prompt.md").exists():      # older runs: take what follows the last input heading
        prompt = (run_dir / "prompt.md").read_text(encoding="utf-8", errors="replace")
        m = list(re.finditer(r"^## The (job posting|job description|change)\s*$", prompt, re.M))
        text = prompt[m[-1].end():] if m else ""
    skip = re.compile(r"^(posted|proposals|payment|file:|```|diff |index |@@|---|\+\+\+)", re.I)
    for line in text.splitlines():
        line = line.strip(" #*\t")
        if line and not skip.match(line):
            # prefer the first sentence; otherwise cut at a word boundary, never mid-word
            first = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
            if len(first) <= 90:
                return first
            return line[:88].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return ""


# Block: everything the page needs to draw one run (None if it predates results.json)
def run_view(name):
    run_dir = RUNS / name
    state_file = run_dir / "results.json"
    if not state_file.exists():
        return None
    state = json.loads(state_file.read_text(encoding="utf-8"))
    thread_file = run_dir / "thread.json"
    thread = json.loads(thread_file.read_text(encoding="utf-8")) if thread_file.exists() else []
    inp = run_dir / "input.txt"
    return {
        "name": name, "task": state["task"], "source": state.get("source", ""),
        "title": run_title(run_dir),
        "input": inp.read_text(encoding="utf-8", errors="replace")[:20000] if inp.exists() else None,
        "answers": [parse_answer(r) for r in state["results"]],
        "compare": parse_compare(state.get("comparison")),
        "thread": [{"question": t["question"], "asked": t.get("asked", ""),
                    "answers": [{"name": r["name"], "status": r["status"], "secs": r.get("secs", 0),
                                 "text": r.get("text", "")} for r in t["results"]]}
                   for t in thread],
    }


# ---------------------------------------------------------------------------
# Running jobs
# ---------------------------------------------------------------------------

# Block: start one ask_all.py run in the background and return its job id.
# Input is checked here so a bad request gets a clear message instead of a failed run.
def start_job(body):
    opts = options()
    task = body.get("task")
    if task not in opts["tasks"]:
        raise ValueError(f"unknown task {task!r}")
    models = [m for m in body.get("models") or [] if m in opts["models"]]
    if not models:
        raise ValueError("pick at least one model")
    text = body.get("text") or ""
    repo = (body.get("repo") or "").strip()

    args = [PYTHON, str(ROOT / "ask_all.py"), task, "--models", ",".join(models)]
    if repo and task == "code-review":
        # code review of a project folder: ask_all runs `git diff` there itself
        args += ["--diff", repo, "--against", (body.get("against") or "HEAD").strip() or "HEAD"]
        stdin = b""
    elif text.strip():
        stdin = text.encode("utf-8")        # pasted text goes in on stdin, like a pipe
    else:
        raise ValueError("paste something first (or give a project folder for a code review)")
    return launch(args, stdin, task, models, None)


# Block: start a follow-up on an existing run; only runs that saved results.json can take one
def start_followup(body):
    run = body.get("run") or ""
    if not RUN_NAME.match(run) or not (RUNS / run / "results.json").exists():
        raise ValueError("this run can't take follow-ups (it was made before follow-ups existed)")
    question = (body.get("question") or "").strip()
    if not question:
        raise ValueError("type a follow-up question first")
    models = [m for m in body.get("models") or [] if m in options()["models"]]
    if not models:
        raise ValueError("pick at least one model")
    with jobs_lock:
        if run in busy_runs:
            raise ValueError("a follow-up on this run is still going; wait for it to finish")
        busy_runs.add(run)
    args = [PYTHON, str(ROOT / "ask_all.py"), "--followup", run, "--models", ",".join(models)]
    return launch(args, question.encode("utf-8"), "follow-up", models, run)


# Block: register a job and start its worker thread
def launch(args, stdin, task, models, run):
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {"status": "running", "started": time.time(), "task": task, "run": run,
                        "models": models, "progress": {m: "asking" for m in models},
                        "phase": "asking", "report": None, "log": ""}
    threading.Thread(target=run_job, args=(job_id, args, stdin), daemon=True).start()
    log(f"job {job_id} started: {task} {models} run={run} {len(stdin)} bytes")
    return job_id


# Block: the background worker: run ask_all.py, reading its output line by line so the
# page can show each model finishing live; then record the run folder or the error
PROGRESS = re.compile(r"^\s+(\S+)\s+(ok|failed|timeout|missing)\s+(\d+)s\s*$")
def run_job(job_id, args, stdin):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    lines = []
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, cwd=ROOT, env=env, creationflags=NO_WINDOW)
        p.stdin.write(stdin)
        p.stdin.close()
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            lines.append(line)
            m = PROGRESS.match(line)
            with jobs_lock:
                if m and m.group(1) in jobs[job_id]["progress"]:
                    jobs[job_id]["progress"][m.group(1)] = f"{m.group(2)} {m.group(3)}s"
                elif line.startswith("Comparing"):
                    jobs[job_id]["phase"] = "comparing"
        p.wait(timeout=1500)
        out = "\n".join(lines)
        m = re.search(r"Report: (.+?report\.html)", out)
        report = Path(m.group(1).strip()).parent.name if m else None
        status = "done" if report and p.returncode == 0 else "failed"
    except Exception as e:                       # never leave a job stuck on "running"
        out, report, status = "\n".join(lines + [f"Server error: {e}"]), None, "failed"
    with jobs_lock:
        jobs[job_id].update(status=status, report=report, log=out.strip(),
                            secs=round(time.time() - jobs[job_id]["started"]))
        busy_runs.discard(jobs[job_id].get("run"))
    log(f"job {job_id} {status} report={report}")


# Block: past runs, newest first, read from the runs/ folder (so they survive restarts).
# Each carries a title and every model's verdict, for the glanceable history list.
def past_runs(limit=60):
    if not RUNS.exists():
        return []
    dirs = sorted((p for p in RUNS.iterdir() if RUN_NAME.match(p.name)
                   and (p / "report.html").exists()), key=lambda p: p.name, reverse=True)
    out = []
    for p in dirs[:limit]:
        verdicts, turns = [], 0
        if (p / "results.json").exists():
            try:
                state = json.loads((p / "results.json").read_text(encoding="utf-8"))
                verdicts = [{"name": r["name"], "status": r["status"],
                             "verdict": labels(r.get("text", "")).get("verdict") if r["status"] == "ok" else None}
                            for r in state["results"]]
                if (p / "thread.json").exists():
                    turns = len(json.loads((p / "thread.json").read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                pass
        n = p.name
        out.append({"name": n, "task": n[16:], "title": run_title(p), "chat": (p / "results.json").exists(),
                    "turns": turns, "verdicts": verdicts,
                    "when": f"{n[0:4]}-{n[4:6]}-{n[6:8]} {n[9:11]}:{n[11:13]}"})
    return out


class Handler(BaseHTTPRequestHandler):
    # Block: refuse anything not addressed to this server by a page from this server
    def _allowed(self):
        if self.headers.get("Host") not in OK_HOSTS:           # blocks DNS-rebinding tricks
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in OK_ORIGINS          # blocks other websites

    def _send(self, code, body, ctype="application/json", cache=False):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        text = ctype.startswith("text") or "json" in ctype
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if text else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    # Block: page files, fonts, API reads, and legacy report pages
    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        path = self.path.split("?")[0]
        if path in STATIC:
            name, ctype = STATIC[path]
            return self._send(200, (WEB / name).read_bytes(), ctype)
        m = re.fullmatch(r"/fonts/([^/]+)", path)
        if m and FONT_NAME.match(m.group(1)) and (WEB / "fonts" / m.group(1)).exists():
            return self._send(200, (WEB / "fonts" / m.group(1)).read_bytes(), "font/ttf", cache=True)
        if path == "/api/options":
            return self._send(200, options())
        if path == "/api/runs":
            return self._send(200, past_runs())
        m = re.fullmatch(r"/api/run/([^/]+)", path)
        if m and RUN_NAME.match(m.group(1)):
            view = run_view(m.group(1))
            return self._send(200, view) if view else self._send(404, {"error": "legacy run", "legacy": True})
        m = re.fullmatch(r"/api/job/([0-9a-f]{12})", path)
        if m:
            with jobs_lock:
                job = json.loads(json.dumps(jobs.get(m.group(1)) or {}))   # a copy, not the live dict
            if not job:
                return self._send(404, {"error": "no such job (was the server restarted?)"})
            job["elapsed"] = round(time.time() - job["started"])
            return self._send(200, job)
        # only real run folders, only report.html: no way to read other files on the PC
        m = re.fullmatch(r"/runs/([^/]+)/report\.html", path)
        if m and RUN_NAME.match(m.group(1)):
            f = RUNS / m.group(1) / "report.html"
            if f.exists():
                return self._send(200, f.read_bytes(), "text/html")
        return self._send(404, {"error": "not found"})

    # Block: start a run or a follow-up. JSON only: a cross-site form can't send JSON
    # without the browser asking permission first, and this server never grants it.
    def do_POST(self):
        starters = {"/api/ask": start_job, "/api/followup": start_followup}
        if not self._allowed() or self.path not in starters:
            return self._send(403, {"error": "forbidden"})
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._send(415, {"error": "JSON only"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            return self._send(200, {"id": starters[self.path](body)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

    # Block: keep per-request noise out of the log; polling would flood it
    def log_message(self, fmt, *args):
        pass


# Block: is our server already up? (so a second double-click just opens the page)
def already_running():
    try:
        with urllib.request.urlopen(URL + "api/options", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


# Block: entry point: reuse a running server or start one, then optionally open the page
def main():
    if sys.stderr is None:                       # pythonw: send stray errors to the log
        sys.stderr = open(LOG, "a", encoding="utf-8")
    want_open = "--open" in sys.argv
    if already_running():
        if want_open:
            webbrowser.open(URL)
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"server started on {URL}")
    if want_open:
        threading.Timer(0.5, webbrowser.open, args=(URL,)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
