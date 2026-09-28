"""
The page server's safety rules, tested against a real running server (on a spare port,
with the Host header the page would send). No AI is called: every request here is one
the server must refuse or answer without starting a run.

What they prove: only requests addressed to this server by a page from this server get
through; other websites, disguised hosts and plain form posts are refused; only the
three page tasks exist; and no file outside the known list can be read.
"""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import server  # noqa: E402

GOOD_HOST = "127.0.0.1:8770"            # what a browser on the real page sends


# Block: a real server on a spare port for the duration of each test
@pytest.fixture
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def request(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers={"Host": GOOD_HOST, **(headers or {})})
    resp = conn.getresponse()
    return resp.status, resp.read()


# Block: the page and its files load
def test_page_and_assets_load(port):
    for path in ("/", "/app.css", "/app.js", "/api/options"):
        assert request(port, "GET", path)[0] == 200, path


def test_only_the_three_page_tasks_are_offered(port):
    _, body = request(port, "GET", "/api/options")
    assert json.loads(body)["tasks"] == ["job-post", "job-fit", "code-review"]


# Block: refusals
def test_disguised_host_is_refused(port):
    assert request(port, "GET", "/api/options", {"Host": "evil.example:8770"})[0] == 403


def test_other_website_cannot_start_a_run(port):
    body = json.dumps({"task": "job-post", "text": "x", "models": ["claude"]})
    status, _ = request(port, "POST", "/api/ask",
                        {"Origin": "https://evil.example", "Content-Type": "application/json"}, body)
    assert status == 403


def test_plain_form_post_is_refused(port):
    status, _ = request(port, "POST", "/api/ask",
                        {"Content-Type": "application/x-www-form-urlencoded"}, "task=job-post")
    assert status == 415


def test_unknown_task_is_refused_without_a_run(port):
    body = json.dumps({"task": "_compare", "text": "x", "models": ["claude"]})
    status, reply = request(port, "POST", "/api/ask", {"Content-Type": "application/json"}, body)
    assert status == 400 and b"unknown task" in reply


@pytest.mark.parametrize("path", ["/server.py", "/models.toml", "/runs/../models.toml/report.html",
                                  "/fonts/..%2Fserver.py", "/fonts/server.py"])
def test_files_outside_the_list_cannot_be_read(port, path):
    assert request(port, "GET", path)[0] == 404
