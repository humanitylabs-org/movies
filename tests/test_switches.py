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


def test_removed_switches_are_gone_and_their_saved_values_pruned(tmp_path):
    # movies#19: the dropped `reply` switch (movies#15) is gone from the list, and a value saved on a box is removed.
    assert [s["id"] for s in server.switches_doc(tmp_path / "none.json")["switches"]] == ["friendly-phone"]
    saved = tmp_path / "switches.json"
    saved.write_text(json.dumps({"reply": True, "friendly-phone": True}))
    server.prune_switches(saved)
    assert json.loads(saved.read_text()) == {"friendly-phone": True}
    server.prune_switches(tmp_path / "missing.json")
    assert not (tmp_path / "missing.json").exists()


def _failing_second_read(monkeypatch, target, fault):
    original_read = Path.read_text
    reads = {"n": 0}

    def read_text(path, *args, **kwargs):
        if path == target:
            reads["n"] += 1
            if reads["n"] == 2:
                if fault == "oserror":
                    raise OSError("synthetic transient read fault")
                return "{not json"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return reads


@pytest.mark.parametrize("fault", ["oserror", "parse"])
def test_prune_leaves_settings_untouched_when_locked_read_fails(monkeypatch, tmp_path, fault):
    # movies#21: healthy first read sees the removed `reply`, the locked read fails; the owner's file must survive.
    target = tmp_path / "switches.json"
    original = json.dumps({"reply": True, "friendly-phone": True}).encode()
    target.write_bytes(original)
    reads = _failing_second_read(monkeypatch, target, fault)
    server.prune_switches(target)
    assert reads["n"] == 2
    assert target.read_bytes() == original
    assert not (tmp_path / "switches.json.tmp").exists()


@pytest.mark.parametrize("value", [True, False])
def test_healthy_prune_keeps_both_friendly_choices(tmp_path, value):
    target = tmp_path / "switches.json"
    target.write_text(json.dumps({"reply": True, "friendly-phone": value}))
    server.prune_switches(target)
    assert json.loads(target.read_text()) == {"friendly-phone": value}
