#!/usr/bin/env python3
"""Browser QA for the friendly-phone switch (movies#14): off keeps the old layout, on moves the controls."""
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
PORT = 8917
BASE = f"http://127.0.0.1:{PORT}/cq-friendly/"
SHOTS = Path(os.environ.get("CQ_SHOTS") or tempfile.gettempdir())


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
        data=json.dumps({"id": "friendly-phone", "on": on}).encode(),
        headers={"Content-Type": "application/json", "X-Wizard-Switch": "1"},
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        assert res.status == 200


def layout(page):
    return page.evaluate(
        """() => {
          const add = document.querySelector('#addOpen');
          const bar = document.querySelector('.mv-bar');
          const menu = document.querySelector('.movie .row-menu');
          const mb = menu && menu.getBoundingClientRect();
          return {
            friendly: document.documentElement.hasAttribute('data-mv-friendly'),
            addInTopbar: !!add.closest('.topbar'),
            addInBar: !!add.closest('.mv-bar'),
            barPosition: bar ? getComputedStyle(bar).position : null,
            barBottom: bar ? Math.round(bar.getBoundingClientRect().bottom) : null,
            toggleInToolbar: !!document.querySelector('.toolbar .viewtoggle'),
            menuSize: mb ? [Math.round(mb.width), Math.round(mb.height)] : null,
            filterLabels: [...document.querySelectorAll('[data-filter]')].map(b => b.textContent.trim()),
            innerHeight,
          };
        }"""
    )


def test_friendly_phone_switch_moves_controls_and_restores_them():
    tmp = Path(tempfile.mkdtemp(prefix="cq-friendly-"))
    (tmp / "movies.json").write_text(json.dumps([
        {"id": "a", "title": "Queue One", "watched": False, "year": 2001},
        {"id": "b", "title": "Queue Two", "watched": False, "year": 2002},
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
        env={**os.environ, "CINEQUEUE_BASE_PATH": "/cq-friendly", "CINEQUEUE_DATA": str(tmp), "CINEQUEUE_WATCH_REGION": ""},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(**playwright.devices.get("Pixel 5", {}))
            page = context.new_page()
            # The shared Wizard app-client only exists inside Wizard; stand in an empty script outside it.
            page.route("**/wizard-os-home/core/app-client.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            # Off (default): the original layout.
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector(".movie")
            off = layout(page)
            assert off["friendly"] is False and off["addInTopbar"] and not off["addInBar"], off
            assert off["toggleInToolbar"] is False and off["filterLabels"] == ["Unwatched", "Watched"], off
            page.screenshot(path=str(SHOTS / "cq-friendly-off-phone.png"))

            # On: thumb bar at the bottom on a phone, Add inside it, row menu a real target.
            flip(True)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("html[data-mv-friendly] .movie")
            on = layout(page)
            assert on["addInBar"] and not on["addInTopbar"] and on["toggleInToolbar"], on
            assert on["barPosition"] == "fixed" and on["barBottom"] == on["innerHeight"], on
            assert on["menuSize"] and on["menuSize"][0] >= 32 and on["menuSize"][1] >= 44, on
            assert on["filterLabels"] == ["Unwatched", "Watched"], on
            page.screenshot(path=str(SHOTS / "cq-friendly-on-phone.png"))
            page.get_by_role("button", name="Watched", exact=True).click()
            assert "Nothing watched yet" in page.locator("#list .empty").inner_text()
            page.get_by_role("button", name="Add", exact=True).click()
            page.wait_for_selector("#addRequestPanel:not([hidden])")
            page.keyboard.press("Escape")

            # Flipped off from elsewhere (the Tracker): becoming visible again restores the old layout, no reload.
            flip(False)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("!document.documentElement.hasAttribute('data-mv-friendly')")
            back = layout(page)
            assert back["addInTopbar"] and not back["addInBar"] and not back["toggleInToolbar"], back
            assert back["filterLabels"] == ["Unwatched", "Watched"], back
            assert page.locator(".filterbar > .filters + .viewtoggle").count() == 1
            page.get_by_role("button", name="Watched", exact=True).click()
            assert page.locator("#list .empty").inner_text() == "No matching films."

            # Wide window with the switch on: same row above the list, not fixed.
            flip(True)
            wide = browser.new_page(viewport={"width": 1280, "height": 800})
            wide.route("**/wizard-os-home/core/app-client.js", lambda r: r.fulfill(body="", content_type="application/javascript"))
            wide.goto(BASE, wait_until="domcontentloaded")
            wide.wait_for_selector("html[data-mv-friendly] .movie")
            w = layout(wide)
            assert w["addInBar"] and w["barPosition"] == "static", w
            wide.screenshot(path=str(SHOTS / "cq-friendly-on-desktop.png"))
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)
