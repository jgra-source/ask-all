"""
The page's Settings: switching models on and off, the Test button, and editing the
profile, tested against a real running server with pretend AIs. Every path points at
throwaway files (ASK_ALL_CONFIG, ASK_ALL_LOCAL, ASK_ALL_PROFILE), so nothing real is touched.

What they prove: on/off choices land in the local choices file, never in models.toml,
and every run sees them; the page cannot slip a new command in; Test reports a
working and a broken model correctly; the first profile save creates the profile from
the example; only the four known files can be written; other websites are refused.
"""
import http.client
import json
import sys
import threading
import tomllib
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
import ask_all  # noqa: E402
import server   # noqa: E402

CONFIG = ROOT / "tests" / "models.test.toml"


# Block: a real server on a spare port, with every settings path pointed at temp files
@pytest.fixture
def port(tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_ALL_CONFIG", str(CONFIG))
    monkeypatch.setenv("ASK_ALL_LOCAL", str(tmp_path / "settings.local.json"))
    monkeypatch.setenv("ASK_ALL_PROFILE", str(tmp_path / "profile"))       # doesn't exist yet
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Host": "127.0.0.1:8770", **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"{}")


# Block: the view lists every model with its status, and flags the example profile
def test_settings_view(port):
    status, view = call(port, "GET", "/api/settings")
    assert status == 200
    models = {m["name"]: m for m in view["models"]}
    assert set(models) == {"fake-a", "fake-b", "fake-broken"}
    assert models["fake-a"]["enabled"] and not models["fake-broken"]["enabled"]
    assert all(m["installed"] for m in models.values())                  # all three are Python
    assert view["profile"]["is_example"] is True
    assert [f["name"] for f in view["profile"]["files"]] == ["about.md", "facts.md", "playbook.md", "code_rules.md"]


# Block: switching a model on lands in the local choices file, not models.toml, and runs see it
def test_toggle_goes_to_local_choices_only(port, tmp_path):
    before = CONFIG.read_bytes()
    status, reply = call(port, "POST", "/api/settings/model", {"name": "fake-broken", "enabled": True})
    assert status == 200 and reply["enabled"] is True
    assert CONFIG.read_bytes() == before                                   # the shared file is untouched
    saved = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert saved == {"models": {"fake-broken": {"enabled": True}}}
    assert "fake-broken" in call(port, "GET", "/api/options")[1]["models"]
    assert call(port, "POST", "/api/settings/model", {"name": "fake-broken", "enabled": False})[0] == 200
    assert "fake-broken" not in call(port, "GET", "/api/options")[1]["models"]


def test_toggle_rejects_bad_input(port):
    assert call(port, "POST", "/api/settings/model", {"name": "no-such-model", "enabled": True})[0] == 400
    assert call(port, "POST", "/api/settings/model", {"name": "fake-a", "enabled": "yes"})[0] == 400


# Block: the page cannot change what a model runs, even if it sends a command
def test_page_cannot_change_a_command(port, tmp_path):
    call(port, "POST", "/api/settings/model", {"name": "fake-a", "enabled": True, "command": ["calc.exe"]})
    saved = json.loads((tmp_path / "settings.local.json").read_text(encoding="utf-8"))
    assert saved["models"]["fake-a"] == {"enabled": True}
    model = next(m for m in ask_all.load_config()["model"] if m["name"] == "fake-a")
    with open(CONFIG, "rb") as f:
        original = next(m for m in tomllib.load(f)["model"] if m["name"] == "fake-a")
    assert model["command"] == original["command"]


# Block: Test tells a working model from a broken one
def test_test_button(port):
    status, ok = call(port, "POST", "/api/settings/test", {"name": "fake-a"})
    assert status == 200 and ok["status"] == "ok"
    status, bad = call(port, "POST", "/api/settings/test", {"name": "fake-broken"})
    assert status == 200 and bad["status"] == "failed"


# Block: the first save creates the profile from the example; later saves just save
def test_profile_save(port, tmp_path):
    status, first = call(port, "POST", "/api/settings/profile", {"file": "about.md", "text": "Name: Test Person"})
    assert status == 200 and first["created"] is True
    profile = tmp_path / "profile"
    assert (profile / "about.md").read_text(encoding="utf-8") == "Name: Test Person"
    assert (profile / "facts.md").exists()                                 # the rest came from the example
    status, second = call(port, "POST", "/api/settings/profile", {"file": "facts.md", "text": "- [f1] a fact"})
    assert status == 200 and second["created"] is False
    assert call(port, "GET", "/api/settings")[1]["profile"]["is_example"] is False


def test_profile_save_only_writes_the_four_files(port, tmp_path):
    for name in ("../escape.md", "models.toml", "notes.txt", "about.md/../x.md"):
        assert call(port, "POST", "/api/settings/profile", {"file": name, "text": "x"})[0] == 400, name
    assert call(port, "POST", "/api/settings/profile", {"file": "about.md", "text": "x" * 300_001})[0] == 400
    assert not (tmp_path / "escape.md").exists()


# Block: another website can't change settings
def test_other_website_cannot_change_settings(port):
    status, _ = call(port, "POST", "/api/settings/model", {"name": "fake-broken", "enabled": True},
                     {"Origin": "https://evil.example"})
    assert status == 403
