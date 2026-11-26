"""
credits.py: checks remaining credits, subscription tier, and quota status for models.

Claude:
  - subscription tier (Pro, Team, etc.) from Claude Code CLI or ~/.claude/.credentials.json
  - prepaid API credits balance from Anthropic OAuth endpoint (cached)
Gemini & Antigravity Claude:
  - Antigravity account tier (Starter Quota / Free tier)
  - Quota exhaustion alerts and remaining reset countdown from recent runs / adapters cache
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = Path(os.environ.get("ASK_ALL_RUNS") or HERE / "runs")
QUOTA_CACHE_FILE = HERE / "adapters" / ".agy-quota.json"
CACHE_TTL = 30  # seconds

_cache = {}
_cache_time = 0
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def format_remaining_time(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    total_mins = int(round(seconds / 60))
    hours = total_mins // 60
    mins = total_mins % 60
    if hours > 0 and mins > 0:
        return f"{hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{mins}m"


def load_quota_cache() -> dict:
    data = {}
    if QUOTA_CACHE_FILE.exists():
        try:
            data = json.loads(QUOTA_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}

    # Check if we should supplement from recent runs
    now = time.time()
    needs_scan = any(
        name not in data or (data[name].get("reset_ts", 0) > 0 and data[name]["reset_ts"] < now)
        for name in ("gemini", "agy-claude")
    )
    if needs_scan and RUNS.is_dir():
        scanned = scan_runs_for_quota()
        for k, v in scanned.items():
            if k not in data or data[k].get("status") != "exhausted":
                data[k] = v

    return data


def save_quota_cache(data: dict):
    try:
        QUOTA_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = QUOTA_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, QUOTA_CACHE_FILE)
    except OSError:
        pass


def record_model_quota_event(model_id: str, status: str, err_msg: str = ""):
    data = load_quota_cache()
    now = time.time()
    if status == "ok":
        data[model_id] = {"status": "ok", "updated_at": now}
    elif status == "exhausted":
        reset_secs = 0
        m = re.search(r"Resets in (\d+)h(?:(\d+)m)?(?:(\d+)s)?", err_msg, re.I)
        if m:
            h = int(m.group(1))
            mins = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            reset_secs = h * 3600 + mins * 60 + s
        data[model_id] = {
            "status": "exhausted",
            "reset_ts": (now + reset_secs) if reset_secs > 0 else 0,
            "error": err_msg,
            "updated_at": now,
        }
    save_quota_cache(data)


def scan_runs_for_quota() -> dict:
    found = {}
    now = time.time()
    if not RUNS.is_dir():
        return found

    for d in sorted(RUNS.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        res_file = d / "results.json"
        if not res_file.exists():
            continue
        try:
            data = json.loads(res_file.read_text(encoding="utf-8"))
            run_name = d.name
            m_time = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", run_name)
            run_ts = None
            if m_time:
                dt = datetime.datetime(*[int(x) for x in m_time.groups()])
                run_ts = dt.timestamp()

            for r in data.get("results", []):
                name = r.get("name")
                if not name or name in found:
                    continue
                text = r.get("text", "")
                if "quota reached" in text.lower() or "quota exceeded" in text.lower():
                    m = re.search(r"Resets in (\d+)h(?:(\d+)m)?(?:(\d+)s)?", text, re.I)
                    if m and run_ts:
                        h = int(m.group(1))
                        mins = int(m.group(2) or 0)
                        s = int(m.group(3) or 0)
                        reset_ts = run_ts + h * 3600 + mins * 60 + s
                        if reset_ts > now:
                            found[name] = {
                                "status": "exhausted",
                                "reset_ts": reset_ts,
                                "error": text.strip()[:200],
                            }
                            continue
                if r.get("status") == "ok":
                    found[name] = {"status": "ok", "updated_at": run_ts or now}
        except Exception:
            pass

        if len(found) >= 3:
            break

    return found


def get_claude_credits() -> dict:
    claude_exe = shutil.which("claude")
    if not claude_exe:
        return {
            "pill": "Not installed",
            "badge": "Not installed",
            "detail": "Claude Code CLI not found on PATH",
            "tone": "bad",
            "quota_exhausted": False,
        }

    creds_path = Path.home() / ".claude" / ".credentials.json"
    oauth_info = {}
    if creds_path.exists():
        try:
            creds = json.loads(creds_path.read_text(encoding="utf-8"))
            oauth_info = creds.get("claudeAiOauth", {})
        except Exception:
            pass

    sub_type = oauth_info.get("subscriptionType", "").capitalize()
    if not sub_type:
        # Run claude auth status if creds file was missing or had no subType
        try:
            p = subprocess.run([claude_exe, "auth", "status"], capture_output=True,
                               timeout=3, creationflags=NO_WINDOW)
            if p.returncode == 0:
                st = json.loads(p.stdout.decode("utf-8", "ignore"))
                sub_type = st.get("subscriptionType", "pro").capitalize()
        except Exception:
            sub_type = "Pro"

    sub_label = f"{sub_type} plan" if sub_type else "Active plan"

    # Check prepaid credits if access token is available
    token = oauth_info.get("accessToken")
    org_id = None
    balance_str = None

    if token:
        # Look for cached model catalog to get org UUID without shelling out
        catalog_dir = Path.home() / ".claude" / "cache" / "model-catalog"
        if catalog_dir.is_dir():
            for f in catalog_dir.glob("*.json"):
                parts = f.stem.split("-")
                if len(parts) >= 5 and len(parts[0]) == 8:
                    org_id = "-".join(parts[:5])
                    break

        if org_id:
            try:
                url = f"https://api.anthropic.com/api/oauth/organizations/{org_id}/prepaid/credits"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "claude-code",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    cdata = json.loads(resp.read().decode("utf-8", "ignore"))
                    amount = cdata.get("amount", 0)
                    currency = cdata.get("currency", "USD")
                    sym = "$" if currency == "USD" else ""
                    balance_str = f"{sym}{amount:.2f}"
            except Exception:
                pass

    if balance_str:
        pill = f"{sub_type} · {balance_str}"
        detail = f"Claude {sub_type} subscription · {balance_str} prepaid credits"
    else:
        pill = f"{sub_type} subscription"
        detail = f"Claude {sub_type} subscription · active sign-in"

    return {
        "pill": pill,
        "badge": sub_label,
        "detail": detail,
        "tone": "good",
        "quota_exhausted": False,
        "tier": sub_type.lower(),
        "balance": balance_str,
    }


def get_antigravity_credits(model_name: str) -> dict:
    quota_cache = load_quota_cache()
    model_quota = quota_cache.get(model_name, {})
    now = time.time()

    reset_ts = model_quota.get("reset_ts", 0)
    if reset_ts and reset_ts > now:
        rem_secs = reset_ts - now
        rem_formatted = format_remaining_time(rem_secs)
        rem_hours = int(round(rem_secs / 3600)) or 1
        return {
            "pill": f"Exhausted ({rem_hours}h)",
            "badge": "Quota Reached",
            "detail": f"Individual quota reached · Resets in {rem_formatted}",
            "tone": "bad",
            "quota_exhausted": True,
            "resets_in_seconds": rem_secs,
            "tier": "free-tier",
        }

    # Query user status tier from Antigravity IDE state database if present
    tier_label = "Starter Quota"
    tier_desc = "Antigravity Starter Quota (Free tier)"

    vscdb_path = Path(os.environ.get("APPDATA", "")) / "Antigravity IDE" / "User" / "globalStorage" / "state.vscdb"
    if vscdb_path.exists():
        try:
            conn = sqlite3.connect(f"file:{vscdb_path}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT value FROM ItemTable WHERE key = ?", ("antigravityUnifiedStateSync.userStatus",))
            row = c.fetchone()
            conn.close()
            if row and "Starter Quota" in row[0]:
                tier_label = "Starter Quota"
                tier_desc = "Antigravity Starter Quota (Free tier)"
        except Exception:
            pass

    if model_name == "agy-claude":
        pill = "Starter (Claude)"
        detail = f"{tier_desc} · Claude Sonnet allocation"
    else:
        pill = "Starter (Free)"
        detail = tier_desc

    return {
        "pill": pill,
        "badge": tier_label,
        "detail": detail,
        "tone": "good",
        "quota_exhausted": False,
        "resets_in_seconds": None,
        "tier": "free-tier",
    }


def get_model_credits(model_name: str) -> dict:
    if model_name == "claude":
        return get_claude_credits()
    elif model_name in ("gemini", "agy-claude"):
        return get_antigravity_credits(model_name)
    elif model_name == "codex":
        return {
            "pill": "Free allowance",
            "badge": "Free allowance",
            "detail": "ChatGPT rolling 5h window & weekly cap",
            "tone": "none",
            "quota_exhausted": False,
        }
    elif model_name == "local-llama":
        return {
            "pill": "Local (Free)",
            "badge": "Local model",
            "detail": "Runs locally on this machine · No quota limit",
            "tone": "good",
            "quota_exhausted": False,
        }
    else:
        return {
            "pill": "Standard",
            "badge": "Active",
            "detail": "Model configured in models.toml",
            "tone": "none",
            "quota_exhausted": False,
        }


def get_all_credits(models=None) -> dict:
    global _cache, _cache_time
    now = time.time()
    if _cache and (now - _cache_time < CACHE_TTL):
        if models is None:
            return dict(_cache)
        return {m: _cache.get(m, get_model_credits(m)) for m in models}

    res = {}
    if models is None:
        models = ["claude", "gemini", "agy-claude", "codex", "local-llama"]
    for m in models:
        res[m] = get_model_credits(m)

    _cache = res
    _cache_time = now
    return res
