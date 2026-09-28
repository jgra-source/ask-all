/**
 * ask-all web — app.js
 * The Ask All page: paste once, several AIs answer, and the page shows a glanceable
 * verdict board (one big coloured verdict per AI + whether they agree), the bottom line,
 * Agree / Disagree / Only-one lists, then every section side by side, folded until asked.
 * Talks only to server.py on this PC (/api/...). No framework and no build step: the
 * state is small (one form, one run, one job), so plain functions that redraw are enough.
 */

// Block: small helpers
const $ = (sel, root = document) => root.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
const TASKS = {
  "job-post": { label: "Job post", hint: "Freelance brief: bid or skip?", placeholder: "Paste the job post here..." },
  "job-fit": { label: "Job fit", hint: "Full-time role: apply or not?", placeholder: "Paste the job description here..." },
  "code-review": { label: "Code review", hint: "Real bugs in a change", placeholder: "Paste the code or diff to review..." },
};
const state = { options: null, runs: [], filter: "all", task: "job-post", models: new Set(), timer: null, current: null };

// Block: browser storage for small conveniences only (last task, theme); the page works without it
function store(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); }
  catch { return null; }
}

// Block: colour meaning of a verdict or agreement word: good (go), care, bad (stop), none
function tone(word) {
  if (!word) return "none";
  if (/skip|must fix|blocker|split|don.?t|reject/i.test(word)) return "bad";
  if (/care|gap|minor|caution|partly|partial|but/i.test(word)) return "care";
  return "good";
}
// long old-style verdicts ("Apply, but address the gaps first. The stack...") get cut to a chip
const shortVerdict = v => v ? v.split(/[.;:]|\s[-–—]\s/)[0].trim().slice(0, 42) : "No verdict";

// Block: talking to the server
async function getJSON(url) {
  const r = await fetch(url);
  const data = await r.json().catch(() => ({}));
  return { ok: r.ok, data };
}
async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return { ok: r.ok, data: await r.json().catch(() => ({})) };
}

// ---------------------------------------------------------------------------
// Markdown: a small, safe renderer (everything is HTML-escaped FIRST, then only
// these patterns become tags): headings, bold/italic/code, lists, tables, quotes, fences
// ---------------------------------------------------------------------------

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
// "Met / Partly / Missing" at the start of an item becomes a coloured badge (job-fit must-haves)
function badges(h) {
  return h.replace(/^(?:<strong>)?(Met|Partly|Partial|Missing)\b(?:<\/strong>)?\s*(?:[:\-–—|]\s*)?/i, (_, w) =>
    `<span class="badge ${/^met/i.test(w) ? "good" : /^miss/i.test(w) ? "bad" : "care"}">${w}</span> `);
}
function table(rows) {
  const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
  const isSep = c => c.length && c.every(x => /^:?-{2,}:?$/.test(x));
  const parsed = rows.map(cells);
  let head = null, body = parsed;
  if (parsed.length > 1 && isSep(parsed[1])) { head = parsed[0]; body = parsed.slice(2); }
  return "<table>" + (head ? "<thead><tr>" + head.map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead>" : "")
    + "<tbody>" + body.filter(r => !isSep(r)).map(r => "<tr>" + r.map(c => `<td>${badges(inline(c))}</td>`).join("") + "</tr>").join("")
    + "</tbody></table>";
}
function md(text) {
  const lines = esc(text || "").replace(/\r/g, "").split("\n");
  const out = [];
  let para = [], list = null, i = 0, m;
  const flush = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {                                   // fenced code
      flush(); close(); const buf = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push("<pre><code>" + buf.join("\n") + "</code></pre>"); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {                            // table
      flush(); close(); const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      out.push(table(rows)); continue;
    }
    if ((m = line.match(/^\s*#{1,6}\s+(.*)$/))) { flush(); close(); out.push(`<h4>${inline(m[1])}</h4>`); i++; continue; }
    if ((m = line.match(/^\s*&gt;\s?(.*)$/))) {                   // quote
      flush(); close(); const buf = [m[1]]; i++;
      while (i < lines.length && (m = lines[i].match(/^\s*&gt;\s?(.*)$/))) { buf.push(m[1]); i++; }
      out.push("<blockquote>" + buf.map(inline).join("<br>") + "</blockquote>"); continue;
    }
    if ((m = line.match(/^(\s*)([-*+•]|\d+[.)])\s+(.*)$/))) {     // list item
      flush(); const type = /\d/.test(m[2]) ? "ol" : "ul";
      if (list !== type) { close(); out.push(`<${type}>`); list = type; }
      const lvl = Math.min(2, Math.floor(m[1].replace(/\t/g, "  ").length / 2));
      out.push(`<li class="l${lvl}">${badges(inline(m[3]))}</li>`); i++; continue;
    }
    if (!line.trim()) { flush(); close(); i++; continue; }
    if (list && /^\s{2,}\S/.test(line)) {                         // wrapped line inside a list item
      out[out.length - 1] = out[out.length - 1].replace(/<\/li>$/, "<br>" + inline(line.trim()) + "</li>"); i++; continue;
    }
    close(); para.push(line); i++;
  }
  flush(); close();
  return out.join("");
}
const bulletCount = t => (t || "").split("\n").filter(l => /^\s*([-*+•]|\d+[.)])\s+/.test(l)).length;

// ---------------------------------------------------------------------------
// Sidebar: glanceable history (task tag, title, one verdict dot per AI)
// ---------------------------------------------------------------------------

function dayLabel(d) {
  const iso = x => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const now = new Date(), y = new Date(Date.now() - 864e5);
  if (d === iso(now)) return "Today";
  if (d === iso(y)) return "Yesterday";
  return new Date(d + "T12:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
async function loadRuns() {
  const { ok, data } = await getJSON("/api/runs");
  if (ok) { state.runs = data; renderSide(); }
}
function renderSide() {
  const filters = [["all", "All"], ...Object.entries(TASKS).map(([k, v]) => [k, v.label])];
  $("#filters").innerHTML = filters.map(([k, label]) =>
    `<button class="chip" data-filter="${k}" aria-pressed="${state.filter === k}">${label}</button>`).join("");
  const runs = state.runs.filter(r => state.filter === "all" || r.task === state.filter);
  let html = "", day = "";
  for (const r of runs) {
    const d = r.when.slice(0, 10);
    if (d !== day) { day = d; html += `<div class="day eyebrow">${dayLabel(d)}</div>`; }
    const dots = (r.verdicts || []).map(v =>
      `<i class="vd ${v.status !== "ok" ? "fail" : tone(v.verdict)}" title="${esc(cap(v.name))}: ${esc(v.status !== "ok" ? "didn't answer" : v.verdict || "no verdict")}"></i>`).join("");
    html += `<button class="hist" data-run="${r.name}" aria-current="${state.current === r.name}">
      <span class="hist-top"><span class="tag ${r.task}">${esc(TASKS[r.task]?.label || r.task)}</span><span class="muted mono">${r.when.slice(11)}</span></span>
      <span class="hist-title">${esc(r.title || "(untitled)")}</span>
      <span class="hist-bottom">${dots}${r.turns ? `<span>· ${r.turns} follow-up${r.turns > 1 ? "s" : ""}</span>` : ""}${r.chat ? "" : "<span>· classic</span>"}</span>
    </button>`;
  }
  $("#history").innerHTML = html || '<p class="muted" style="padding:8px 4px">Nothing here yet.</p>';
}

// ---------------------------------------------------------------------------
// Composer: pick a task, paste, pick AIs, Ask
// ---------------------------------------------------------------------------

function showComposer() {
  state.current = null; renderSide();
  const tasks = state.options.tasks;
  $("#main").innerHTML = `
    <section class="composer">
      <h1>What should they look at?</h1>
      <p class="lead">Paste once. Each AI answers on its own, then you see where they agree.</p>
      <div class="tasks" role="group" aria-label="Task">
        ${tasks.map(t => `<button class="task" data-task="${t}" aria-pressed="${t === state.task}"><b>${TASKS[t].label}</b><span>${TASKS[t].hint}</span></button>`).join("")}
      </div>
      <textarea class="input" id="text" spellcheck="false"></textarea>
      <div class="repo" id="repo">
        <input class="field grow" id="repoPath" placeholder="...or a project folder, to review its changes (leave the box above empty)">
        <input class="field" id="against" value="HEAD" title="HEAD = changes not committed yet. main = a whole branch." style="width:150px">
      </div>
      <div class="bar">
        ${state.options.models.map(m => `<button class="pill" data-model="${m}" aria-pressed="${state.models.has(m)}"><span class="dot ${m}"></span>${cap(m)}</button>`).join("")}
        <span class="spacer"></span>
        <span class="error" id="err"></span>
        <span class="muted" style="font-size:12px"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span>
        <button class="primary" id="askBtn">Ask</button>
      </div>
    </section>`;
  applyTask();
  $("#text").focus();
}
// switching task keeps whatever was already pasted
function applyTask() {
  document.querySelectorAll(".task").forEach(b => b.setAttribute("aria-pressed", b.dataset.task === state.task));
  const text = $("#text");
  text.placeholder = TASKS[state.task].placeholder;
  text.classList.toggle("code", state.task === "code-review");
  $("#repo").classList.toggle("show", state.task === "code-review");
  const n = state.models.size;
  $("#askBtn").textContent = n > 1 ? `Ask ${n} AIs` : "Ask";
  store("task", state.task);
}
async function submitAsk() {
  const text = $("#text").value, repo = $("#repoPath")?.value.trim() || "";
  $("#err").textContent = "";
  const { ok, data } = await postJSON("/api/ask", {
    task: state.task, text, repo: state.task === "code-review" && !text.trim() ? repo : "",
    against: $("#against")?.value || "HEAD", models: [...state.models],
  });
  if (!ok) { $("#err").textContent = data.error || "Couldn't start."; return; }
  $("#main").innerHTML = `<div id="prog"></div>`;
  watchJob(data.id, $("#prog"), job => { loadRuns(); location.hash = "#run=" + job.report; });
}

// ---------------------------------------------------------------------------
// Progress: one row per AI, ticking over as each finishes, then the comparison
// ---------------------------------------------------------------------------

function renderProgress(job, target) {
  const rows = job.models.map(m => {
    const p = job.progress?.[m] || "asking";
    const done = !p.startsWith("asking"), good = p.startsWith("ok");
    return `<div class="step ${done ? (good ? "ok" : "bad") : ""}"><span class="dot ${m}"></span>${cap(m)}
      <span class="state">${done ? (good ? "✓ " : "✗ ") + esc(p.replace(/^ok\s*/, "")) : '<span class="spin"></span>'}</span></div>`;
  });
  if (job.task !== "follow-up" && job.models.length > 1) {
    const comparing = job.phase === "comparing";
    rows.push(`<div class="step"><span class="dot"></span>Comparing answers
      <span class="state">${comparing ? '<span class="spin"></span>' : "waiting"}</span></div>`);
  }
  target.innerHTML = `<div class="progress"><h2>${job.task === "follow-up" ? "Asking the follow-up" : "Asking"} <span class="mono muted">${job.elapsed}s</span></h2>${rows.join("")}
    <p class="muted" style="font-size:12px;margin-top:10px">Usually about a minute. You can keep reading history meanwhile.</p></div>`;
}
function watchJob(id, target, onDone) {
  clearInterval(state.timer);
  const tick = async () => {
    const { ok, data: job } = await getJSON(`/api/job/${id}`);
    if (!ok) { clearInterval(state.timer); target.innerHTML = `<p class="error">${esc(job.error || "Lost track of the job.")}</p>`; return; }
    if (!document.body.contains(target)) return;          // user navigated away; keep polling quietly
    if (job.status === "running") return renderProgress(job, target);
    clearInterval(state.timer);
    if (job.status === "done") onDone(job);
    else target.innerHTML = `<div class="banner bad"><b>It didn't work.</b>What the tool said:<pre class="mono" style="white-space:pre-wrap;margin-top:6px">${esc(job.log)}</pre></div>`;
  };
  tick(); state.timer = setInterval(tick, 1500);
}

// ---------------------------------------------------------------------------
// Run view: verdict board -> bottom line -> agree/disagree/only-one -> side by side -> conversation
// ---------------------------------------------------------------------------

async function openRun(name) {
  state.current = name; renderSide();
  const { ok, data } = await getJSON(`/api/run/${encodeURIComponent(name)}`);
  if (!ok && data.legacy) {                   // made before the structured view existed
    const r = state.runs.find(x => x.name === name) || {};
    $("#main").innerHTML = `<div class="run-head"><div class="meta"><span class="tag ${r.task || ""}">${esc(TASKS[r.task]?.label || r.task || "")}</span><span>${esc(r.when || "")}</span></div>
      <h1>${esc(r.title || name)}</h1><p class="muted" style="margin-top:6px">An early run, shown in the classic layout. It can't take follow-ups.</p></div>
      <iframe class="legacy" src="/runs/${name}/report.html"></iframe>`;
    return;
  }
  if (!ok) { $("#main").innerHTML = `<p class="error">${esc(data.error || "Couldn't load that run.")}</p>`; return; }
  renderRun(data);
}

function verdictCard(a) {
  if (a.status !== "ok") return `<div class="vcard bad"><div class="who"><span class="dot ${a.name}"></span>${cap(a.name)}<span class="mono">${a.secs}s</span></div>
    <div class="verdict">Didn't answer</div><div class="why">${esc((a.text || "").split("\n").find(l => l.trim()) || a.status)}</div></div>`;
  const t = tone(a.verdict);
  const why = a.why || (a.verdict && a.verdict.length > 42 ? a.verdict : "");
  return `<div class="vcard ${t}"><div class="who"><span class="dot ${a.name}"></span>${cap(a.name)}<span class="mono">${a.secs}s</span></div>
    <div class="verdict">${esc(shortVerdict(a.verdict))}</div><div class="why" title="${esc(why)}">${esc(why)}</div></div>`;
}
function agreementCard(v) {
  const verdicts = v.answers.filter(a => a.status === "ok").map(a => tone(a.verdict));
  if (verdicts.length < 2) return "";
  let word = v.compare?.agreement;
  if (!word) word = verdicts.every(t => t === verdicts[0]) ? "Same verdict" : "Split";
  const t = /^agree|same/i.test(word) ? "good" : tone(word);
  return `<div class="vcard agreement ${t}"><div class="who">Do they agree?${v.compare ? `<span class="mono">by ${cap(v.compare.by)}</span>` : ""}</div>
    <div class="verdict">${esc(word)}</div><div class="why">${v.compare ? "Details below." : "Compared by verdict only."}</div></div>`;
}

// Block: line sections up across AIs by heading ("Pain points" next to "Pain points")
function alignSections(answers) {
  const norm = t => t.toLowerCase().replace(/[^a-z ]/g, "").replace(/\s+/g, " ").trim();
  const order = [], rows = {};
  const add = (key, title, who, body) => {
    if (!rows[key]) { rows[key] = { title, cells: {} }; order.push(key); }
    rows[key].cells[who] = body;
  };
  for (const a of answers) {
    if (a.status !== "ok") continue;
    if (a.preamble) add("_intro", "Intro", a.name, a.preamble);
    for (const s of a.sections) add(norm(s.title), s.title, a.name, s.body);
  }
  return order.map(k => rows[k]);
}

function renderRun(v) {
  const task = TASKS[v.task] || { label: v.task };
  const names = v.answers.map(a => a.name);
  const sus = v.answers.filter(a => a.suspicious), warn = v.answers.filter(a => a.warning);
  const c = v.compare;
  const rows = alignSections(v.answers);
  const when = (v.source.match(/(\d{4}-\d\d-\d\d \d\d:\d\d)/) || [])[1] || "";

  $("#main").innerHTML = `
    <header class="run-head">
      <div class="meta"><span class="tag ${v.task}">${esc(task.label)}</span><span class="mono">${esc(when)}</span>
        <a class="linkish" href="/runs/${v.name}/report.html" target="_blank">Classic report</a></div>
      <h1>${esc(v.title || task.label)}</h1>
      ${v.input ? `<details class="input-view"><summary>Show what was pasted</summary><pre>${esc(v.input)}</pre></details>` : ""}
    </header>

    ${sus.length ? `<div class="banner bad"><b>Hidden instructions for AIs found in the input</b>
      ${sus.map(a => cap(a.name)).join(" and ")} flagged ${sus.length > 1 ? "them" : "it"} and did not follow ${sus.length > 1 ? "them" : "it"}. Treat the post with suspicion.
      <details><summary>Show what they quoted</summary><div class="md">${md(sus[0].suspicious)}</div></details></div>` : ""}
    ${warn.map(a => `<div class="banner care"><b>${cap(a.name)} used a tool it was told not to</b>${esc(a.warning.replace(/^WARNING:\s*/, ""))}</div>`).join("")}

    <div class="board">${v.answers.map(verdictCard).join("")}${agreementCard(v)}</div>

    ${c && c.bottom ? `<div class="bottom"><div class="eyebrow">Bottom line</div><div class="md">${md(c.bottom)}</div></div>` : ""}
    ${c && (c.agree || c.disagree || c.only) ? `<div class="tri">
      <section class="agree"><h3>✓ Agree on <span class="count">${bulletCount(c.agree)}</span></h3><div class="md">${md(c.agree || "—")}</div></section>
      <section class="disagree"><h3>≠ Disagree on <span class="count">${bulletCount(c.disagree)}</span></h3><div class="md">${md(c.disagree || "Nothing.")}</div></section>
      <section class="only"><h3>◐ Only one caught <span class="count">${bulletCount(c.only)}</span></h3><div class="md">${md(c.only || "—")}</div></section>
    </div>` : ""}

    ${rows.length ? `<div class="rows-head"><h2>Side by side</h2><span class="muted" style="font-size:12.5px">${rows.length} sections</span>
      <span class="spacer"></span><button class="linkish" id="expandAll">Expand all</button></div>
    <div class="rows" style="--cols:${names.length}">
      <div class="row colhead"><span></span>${names.map(n => `<div><span class="dot ${n}"></span>${cap(n)}</div>`).join("")}</div>
      ${rows.map(r => `<div class="row"><div class="rtitle">${esc(r.title)}</div>
        ${names.map(n => r.cells[n] != null
          ? `<div class="cell clamp" data-who="${cap(n)}"><div class="md">${md(r.cells[n])}</div><button class="more">Show more</button></div>`
          : `<div class="cell missing" data-who="${cap(n)}">—</div>`).join("")}</div>`).join("")}
    </div>` : ""}

    <section class="convo">
      ${v.thread.length ? `<h2>Follow-ups</h2>` : ""}
      ${v.thread.map((t, i) => `<div class="turn">
        <div class="q"><span class="muted">You · follow-up ${i + 1} · ${esc(t.asked)}</span>${esc(t.question)}</div>
        <div class="answers">${t.answers.map(a => `<div class="acard ${a.name} ${a.status === "ok" ? "clamp" : ""}">
          <div class="who"><span class="dot ${a.name}"></span>${cap(a.name)}<span class="mono">${a.status === "ok" ? a.secs + "s" : "didn't answer"}</span></div>
          <div class="md">${md(a.text)}</div>${a.status === "ok" ? '<button class="more">Show more</button>' : ""}</div>`).join("")}</div>
      </div>`).join("")}
      <div id="replyArea">
        <div class="reply">
          <textarea id="fq" placeholder="Ask a follow-up. Every AI sees the whole conversation, including the other's answers."></textarea>
          <div class="bar"><span class="error" id="ferr"></span><span class="spacer"></span>
            <span class="muted" style="font-size:12px"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span>
            <button class="primary" id="sendBtn">Send follow-up</button></div>
        </div>
      </div>
    </section>`;
  fitClamps();
  window.scrollTo(0, 0);
}

// Block: fold long text; short cells lose the fold and the "Show more" button
function fitClamps() {
  document.querySelectorAll(".cell.clamp, .acard.clamp").forEach(el => {
    const body = el.querySelector(".md");
    if (body.scrollHeight <= 200) { el.classList.remove("clamp"); el.querySelector(".more")?.remove(); }
  });
}

async function submitFollowup() {
  const question = $("#fq").value.trim();
  $("#ferr").textContent = "";
  const { ok, data } = await postJSON("/api/followup", { run: state.current, question, models: [...state.models] });
  if (!ok) { $("#ferr").textContent = data.error || "Couldn't send."; return; }
  const name = state.current;
  $("#replyArea").innerHTML = `<div id="fprog"></div>`;
  watchJob(data.id, $("#fprog"), async () => {
    await loadRuns(); await openRun(name);
    document.querySelector(".turn:last-of-type")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

// ---------------------------------------------------------------------------
// Wiring: one click handler for the whole page, keyboard shortcuts, routing, theme
// ---------------------------------------------------------------------------

document.addEventListener("click", e => {
  const t = e.target.closest("button, [data-run]");
  if (!t) return;
  if (t.dataset.task) { state.task = t.dataset.task; applyTask(); }
  else if (t.dataset.model) {
    state.models.has(t.dataset.model) ? state.models.delete(t.dataset.model) : state.models.add(t.dataset.model);
    t.setAttribute("aria-pressed", state.models.has(t.dataset.model)); if ($("#askBtn")) applyTask();
  }
  else if (t.dataset.filter) { state.filter = t.dataset.filter; renderSide(); }
  else if (t.dataset.run) { location.hash = "#run=" + t.dataset.run; }
  else if (t.id === "newBtn") { location.hash = "#new"; }
  else if (t.id === "askBtn") submitAsk();
  else if (t.id === "sendBtn") submitFollowup();
  else if (t.id === "themeBtn") cycleTheme();
  else if (t.classList.contains("more")) {
    const box = t.parentElement; box.classList.toggle("clamp");
    t.textContent = box.classList.contains("clamp") ? "Show more" : "Show less";
  }
  else if (t.id === "expandAll") {
    const open = t.textContent === "Expand all";
    document.querySelectorAll(".cell .more, .acard .more").forEach(b => {
      b.parentElement.classList.toggle("clamp", !open); b.textContent = open ? "Show less" : "Show more";
    });
    t.textContent = open ? "Collapse all" : "Expand all";
  }
});
document.addEventListener("keydown", e => {
  if (e.key !== "Enter" || !e.ctrlKey) return;
  if (e.target.id === "text") submitAsk();
  if (e.target.id === "fq") submitFollowup();
});

function route() {
  const h = decodeURIComponent(location.hash);
  if (h.startsWith("#run=")) openRun(h.slice(5));
  else showComposer();
}
window.addEventListener("hashchange", route);

// theme: auto (follow Windows) -> dark -> light
function applyTheme() {
  const t = store("theme") || "auto";
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  $("#themeBtn").title = `Theme: ${t} (click to change)`;
  $("#themeBtn").textContent = t === "light" ? "☀" : t === "dark" ? "☾" : "◐";
}
function cycleTheme() {
  const next = { auto: "dark", dark: "light", light: "auto" }[store("theme") || "auto"];
  store("theme", next); applyTheme();
}

// Block: start-up: options, default task and models, history, then the current view
(async function init() {
  applyTheme();
  const { ok, data } = await getJSON("/api/options");
  if (!ok) { $("#main").innerHTML = '<p class="error">Can\'t reach the Ask All server. Double-click the desktop shortcut to start it.</p>'; return; }
  state.options = data;
  const saved = store("task");
  state.task = data.tasks.includes(saved) ? saved : data.tasks[0];
  state.models = new Set(data.models);
  await loadRuns();
  route();
})();
