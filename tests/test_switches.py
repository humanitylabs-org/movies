"""Owner feature switches (wizard.app-switches/1): off by default, state on the box, owner-only flips."""
from __future__ import annotations

import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import cinequeue.server as server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def base(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DATA", tmp_path / "movies.json")
    monkeypatch.setattr(server, "SWITCHES_FILE", tmp_path / "switches.json")
    monkeypatch.setattr(server, "BASE_PATH", "")
    handler = lambda *a, **k: server.CineQueueHandler(*a, directory=str(server.STATIC), **k)  # noqa: E731
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(url, body=None, headers=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = Request(url, data=data, headers=headers or {}, method="GET" if body is None else "POST")
    try:
        with urlopen(req) as res:
            return res.status, json.loads(res.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def flip(base, switch_id, on, **extra):
    headers = {"Content-Type": "application/json", "X-Wizard-Switch": "1", **extra}
    return call(f"{base}/api/switches", {"id": switch_id, "on": on}, headers)


def test_switches_listing_is_off_by_default_and_linked_to_issue(base):
    status, doc = call(f"{base}/api/switches")
    assert status == 200
    assert doc["contract"] == "wizard.app-switches/1"
    friendly = {s["id"]: s for s in doc["switches"]}["friendly-phone"]
    assert friendly["on"] is False and friendly["default"] is False
    assert friendly["issues"] == ["humanitylabs-org/movies#14"]
    assert friendly["name"] and friendly["description"]


def test_owner_flip_persists_on_the_box(base, tmp_path):
    status, doc = flip(base, "friendly-phone", True)
    assert status == 200
    assert {s["id"]: s["on"] for s in doc["switches"]}["friendly-phone"] is True
    assert json.loads((tmp_path / "switches.json").read_text()) == {"friendly-phone": True}
    assert {s["id"]: s["on"] for s in call(f"{base}/api/switches")[1]["switches"]}["friendly-phone"] is True
    status, doc = flip(base, "friendly-phone", False)
    assert status == 200 and {s["id"]: s["on"] for s in doc["switches"]}["friendly-phone"] is False


def test_flip_needs_switch_header_json_and_known_id(base, tmp_path):
    status, _ = call(f"{base}/api/switches", {"id": "friendly-phone", "on": True}, {"Content-Type": "application/json"})
    assert status == 403
    status, _ = flip(base, "nope", True)
    assert status == 404
    status, _ = flip(base, "friendly-phone", "yes")
    assert status == 400
    status, _ = call(f"{base}/api/switches", b"{not json", {"Content-Type": "application/json", "X-Wizard-Switch": "1"})
    assert status == 400
    assert not (tmp_path / "switches.json").exists()


def test_flip_refuses_foreign_origin_and_cross_site(base, tmp_path):
    status, _ = flip(base, "friendly-phone", True, Origin="https://evil.example")
    assert status == 403
    status, _ = flip(base, "friendly-phone", True, **{"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _ = call(f"{base}/api/switches", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert not (tmp_path / "switches.json").exists()


def test_wizard_md_declares_the_switch_contract():
    text = (ROOT / "wizard.md").read_text()
    assert "switches:\n  contract: wizard.app-switches/1\n  path: /api/switches\n" in text


def _top_level_split(text):
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def test_friendly_css_is_scoped_to_the_switch():
    css = (ROOT / "web" / "movies-skin.css").read_text()
    tail = re.sub(r"/\*.*?\*/", "", css.split("friendly-phone switch", 1)[1].split("*/", 1)[1], flags=re.S)
    groups = [g.strip() for g in re.findall(r"([^{}]+)\{", tail) if not g.strip().startswith("@")]
    assert len(groups) > 20
    for group in groups:
        for selector in _top_level_split(group):
            assert selector.strip().startswith("html[data-mv-friendly]"), selector


def test_reply_switch_is_off_by_default_linked_and_flippable(base, tmp_path):
    reply = {s["id"]: s for s in call(f"{base}/api/switches")[1]["switches"]}["reply"]
    assert reply["on"] is False and reply["default"] is False
    assert reply["issues"] == ["humanitylabs-org/movies#15"]
    assert reply["name"] and reply["description"]
    status, doc = flip(base, "reply", True)
    assert status == 200 and {s["id"]: s["on"] for s in doc["switches"]}["reply"] is True
    assert json.loads((tmp_path / "switches.json").read_text()) == {"reply": True}


def test_reply_button_is_gated_by_the_switch():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "const canReply = () => appSwitches.reply === true && (replyHosted() || onItsOwn());" in app
    assert "${canReply() ? '<button class=\"detail-reply\"" in app
