#!/usr/bin/env python3
"""Browser QA for the reply switch (movies#15): off shows no Reply; on, Reply opens an unsent Chats draft only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    import pytest
    pytest.skip("Playwright is unavailable; run this gate in the configured browser venv", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
PORT = 8918
BASE = f"http://127.0.0.1:{PORT}/cq-reply/"
SHOTS = Path(os.environ.get("CQ_SHOTS") or tempfile.gettempdir())

# A stand-in for the shell's app-client: records every draft and every send-like call.
FAKE_CLIENT = """
window.__drafts = []; window.__sends = [];
window.WizardApp = {
  ready: () => Promise.resolve({}),
  back: { register() {}, useHistory() {} },
  share: { text: async () => 'copied' },
  update: { hold() { return { release() {} }; } },
  nav: {
    openContextDraft(ctx) { window.__drafts.push(ctx); return { kind: 'unsent-draft', sent: false, id: 'movies:' + ctx.id }; },
    openThread() { window.__sends.push('openThread'); },
  },
};
"""


def wait_server():
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE, timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("temporary Movies server did not start")


def flip(on: bool):
    req = urllib.request.Request(
        BASE + "api/switches",
        data=json.dumps({"id": "reply", "on": on}).encode(),
        headers={"Content-Type": "application/json", "X-Wizard-Switch": "1"},
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        assert res.status == 200


def open_card(page):
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector(".movie")
    page.locator(".movie").first.click()
    page.wait_for_selector("#detail:not(.hidden) .detail-share")


def test_reply_switch_gates_the_button_and_only_opens_an_unsent_draft():
    tmp = Path(tempfile.mkdtemp(prefix="cq-reply-"))
    (tmp / "movies.json").write_text(json.dumps([
        {"id": "batman-begins-2005", "title": "Batman Begins", "watched": False, "year": 2005,
         "genres": ["Action"], "directors": ["Christopher Nolan"], "description": "A young Bruce Wayne."},
    ]))
    wrapper = tmp / "serve.py"
    wrapper.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import cinequeue.server as s\n"
        f"s.run(port={PORT})\n"
    )
    server = subprocess.Popen(
        [sys.executable, str(wrapper)],
        env={**os.environ, "CINEQUEUE_BASE_PATH": "/cq-reply", "CINEQUEUE_DATA": str(tmp), "CINEQUEUE_WATCH_REGION": ""},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.route("**/wizard-os-home/core/app-client.js",
                       lambda r: r.fulfill(body=FAKE_CLIENT, content_type="application/javascript"))
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            writes = []
            page.on("request", lambda req: writes.append(req.url) if req.method != "GET" else None)

            # Off (default): no Reply, even though the shell can open drafts.
            open_card(page)
            assert page.locator(".detail-reply").count() == 0
            page.screenshot(path=str(SHOTS / "cq-reply-off.png"))

            # On: Reply sits right after Share movie and opens one unsent draft with the title attached.
            flip(True)
            open_card(page)
            assert page.locator(".detail-links .detail-share + .detail-reply").count() == 1
            page.get_by_role("button", name="Reply", exact=True).click()
            status = page.locator(".detail-reply-status")
            assert status.inner_text() == "Draft opened in Chats with this title attached. Nothing sent."
            drafts = page.evaluate("window.__drafts")
            assert len(drafts) == 1 and drafts[0]["id"] == "title-batman-begins-2005", drafts
            assert drafts[0]["title"] == "Movies: Batman Begins (2005)"
            assert "Director: Christopher Nolan" in drafts[0]["message"] and "A young Bruce Wayne." in drafts[0]["message"]
            assert page.evaluate("window.__sends") == []
            assert writes == [], writes  # the tap itself makes no request at all
            page.screenshot(path=str(SHOTS / "cq-reply-on.png"))
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)
