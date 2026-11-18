"""movies#23: adding a title that is already in the list bumps it to the top (switch bump-existing, off)."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

import cinequeue.server as server
from cinequeue import mcp_api
from cinequeue.core import MovieStore


def _seed(path):
    store = MovieStore(path)
    store.add_many([{"title": "Her", "year": 2013}, {"title": "Heat", "year": 1995}, {"title": "Stalker", "year": 1979}])
    return store  # queue order: Stalker, Heat, Her


def _queue(store):
    return [m["id"] for m in store.all_projected() if m["queued"] and not m.get("archived")]


def test_switch_is_listed_off_and_linked_to_the_issue(tmp_path):
    doc = {s["id"]: s for s in server.switches_doc(tmp_path / "switches.json")["switches"]}
    assert doc["bump-existing"]["on"] is False and doc["bump-existing"]["default"] is False
    assert doc["bump-existing"]["issues"] == ["humanitylabs-org/movies#23"]
    assert server.switch_on("bump-existing", tmp_path / "switches.json") is False
    server.set_switch("bump-existing", True, tmp_path / "switches.json")
    assert server.switch_on("bump-existing", tmp_path / "switches.json") is True


def test_off_leaves_an_existing_title_where_it_is(tmp_path):
    store = _seed(tmp_path / "movies.json")
    result = store.add_or_bump([{"title": "Her", "year": None}])
    assert result == {"added": [], "bumped": []}
    assert _queue(store) == ["stalker-1979", "heat-1995", "her-2013"]


def test_on_bumps_a_queued_title_without_duplicating_it(tmp_path):
    store = _seed(tmp_path / "movies.json")
    result = store.add_or_bump([{"title": "Her", "year": None, "poster": "her.jpg"}], bump_existing=True)
    assert result["added"] == []
    assert [(m["id"], m["was"]) for m in result["bumped"]] == [("her-2013", "queued")]
    assert _queue(store) == ["her-2013", "stalker-1979", "heat-1995"]
    assert len(store.all()) == 3
    assert store.get("her-2013")["poster"] == "her.jpg"


def test_on_requeues_a_watched_title_keeping_history(tmp_path):
    store = _seed(tmp_path / "movies.json")
    store.mark_watched("her-2013", "2026-01-02")
    result = store.add_or_bump([{"title": "Her", "year": 2013}], bump_existing=True)
    assert result["bumped"][0]["was"] == "watched"
    her = store.get_projected("her-2013")
    assert her["queued"] is True and her["watch_count"] == 1
    assert her["watch_history"][0]["watched_on"] == "2026-01-02"
    assert _queue(store)[0] == "her-2013"


def test_on_restores_an_archived_title_then_bumps_it(tmp_path):
    store = _seed(tmp_path / "movies.json")
    store.set_archived("her-2013", True)
    result = store.add_or_bump([{"title": "Her", "year": None}], bump_existing=True)
    assert result["bumped"][0]["was"] == "archived"
    her = store.get("her-2013")
    assert her["archived"] is False and "archived_at" not in her
    assert _queue(store)[0] == "her-2013"


def test_on_new_titles_still_add_and_a_repeated_new_title_is_not_reported_as_bumped(tmp_path):
    store = _seed(tmp_path / "movies.json")
    result = store.add_or_bump([{"title": "Alien", "year": 1979}, {"title": "Alien", "year": 1979}], bump_existing=True)
    assert [m["id"] for m in result["added"]] == ["alien-1979"] and result["bumped"] == []


def test_mcp_add_bumps_only_when_the_switch_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DATA", tmp_path / "movies.json")
    monkeypatch.setattr(server, "SWITCHES_FILE", tmp_path / "switches.json")
    store = _seed(tmp_path / "movies.json")
    off = mcp_api.call("cinequeue_add", {"title": "Her"})
    assert off == {"added": [], "count": 0} and _queue(store)[0] == "stalker-1979"
    server.set_switch("bump-existing", True)
    on = mcp_api.call("cinequeue_add", {"title": "Her"})
    assert on["count"] == 0 and [m["id"] for m in on["bumped"]] == ["her-2013"]
    assert "now first" in on["note"] and _queue(store)[0] == "her-2013"


def test_add_request_prompt_carries_the_rule_only_when_on(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SWITCHES_FILE", tmp_path / "switches.json")
    assert "cinequeue_watch_again" not in server.build_cinequeue_add_prompt("Her")
    server.set_switch("bump-existing", True)
    prompt = server.build_cinequeue_add_prompt("Her")
    for tool in ("cinequeue_bump", "cinequeue_watch_again", "cinequeue_archive"):
        assert tool in prompt
    assert "now first" in prompt
    assert "cinequeue_bump" not in server.build_cinequeue_add_prompt("Her", bump_existing=False)


@pytest.fixture
def base(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DATA", tmp_path / "movies.json")
    monkeypatch.setattr(server, "SWITCHES_FILE", tmp_path / "switches.json")
    monkeypatch.setattr(server, "BASE_PATH", "")
    _seed(tmp_path / "movies.json")
    handler = lambda *a, **k: server.CineQueueHandler(*a, directory=str(server.STATIC), **k)  # noqa: E731
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _add(base, text):
    req = Request(f"{base}/api/add", data=json.dumps({"text": text}).encode(), method="POST",
                  headers={"Content-Type": "application/json", "Origin": base})
    with urlopen(req) as res:
        return json.loads(res.read())


def test_quick_add_endpoint_reports_bumped_titles_when_on(base, tmp_path):
    assert _add(base, "Her") == {"added": [], "count": 0, "bumped": []}
    server.set_switch("bump-existing", True)
    body = _add(base, "Her, Alien 1979")
    assert body["count"] == 1 and [m["id"] for m in body["bumped"]] == ["her-2013"]
    assert _queue(MovieStore(tmp_path / "movies.json"))[:2] == ["alien-1979", "her-2013"]
