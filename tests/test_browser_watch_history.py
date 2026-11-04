#!/usr/bin/env python3
"""Mobile browser lifecycle QA for independent queue membership and watch history."""
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
PORT = 8915
BASE = f"http://127.0.0.1:{PORT}/cq-history/"
SCREENSHOT = Path("/tmp/cq-watch-history-mobile.png")
DETAIL_SCREENSHOT = Path("/tmp/cq-watch-history-detail-mobile.png")


def wait_server():
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE, timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("temporary Movies history server did not start")


def test_watch_history_mobile_lifecycle():
    tmp = Path(tempfile.mkdtemp(prefix="cq-history-"))
    data = tmp / "movies.json"
    fixture = [
        {
            "id": "oldest", "title": "Oldest Watch", "watched": True,
            "watched_at": "2026-06-01T09:00:00+00:00", "year": 2001,
            "rating": 4.9, "rating_count": 999,
        },
        {"id": "queued", "title": "Queue Only", "watched": False, "year": 2002},
        {
            "id": "latest", "title": "Latest Watch", "watched": True,
            "watched_at": "2026-07-25T09:00:00+00:00", "year": 2003,
            "rating": 8.4, "rating_count": 234567, "rating_source": "imdb", "imdb_id": "tt1234567",
            "notes": "Start with these episodes.",
            "featured_episodes": [
                {"title": "Zima Blue", "season": 1, "episode": 14, "runtime_minutes": 10},
                {"title": "Beyond the Aquila Rift", "season": 1, "episode": 7, "runtime_minutes": 17},
            ],
        },
        {
            "id": "multi", "title": "Multiple Watches", "watched": True, "queued": False, "year": 2004,
            "watch_history": [
                {"id": "watch-multi-new", "watched_on": "2026-07-20", "recorded_at": "2026-07-20T10:00:00+00:00"},
                {"id": "watch-multi-old", "watched_on": "2026-05-01", "recorded_at": "2026-05-01T10:00:00+00:00"},
            ],
        },
    ]
    data.write_text(json.dumps(fixture))
    wrapper = tmp / "serve.py"
    wrapper.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import cinequeue.server as s\n"
        f"s.DATA = Path({str(data)!r})\n"
        f"s.run(port={PORT})\n"
    )
    server = subprocess.Popen(
        [sys.executable, str(wrapper)],
        env={**os.environ, "CINEQUEUE_BASE_PATH": "/cq-history", "CINEQUEUE_DATA": str(tmp), "CINEQUEUE_WATCH_REGION": ""},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_server()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(**playwright.devices.get("Pixel 5", {}))
            page = context.new_page()
            errors = []
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_selector(".movie")

            assert page.locator(".movie").count() == 1
            assert page.locator(".movie").first.get_attribute("data-movie-id") == "queued"
            assert page.locator(".rank-cell, .number").count() == 0

            page.get_by_role("button", name="Watched", exact=True).click()
            page.wait_for_timeout(100)
            ids = page.locator(".movie").evaluate_all("rows => rows.map(row => row.dataset.movieId)")
            assert ids == ["latest", "multi", "oldest"], ids
            assert page.locator("#sort").input_value() == "watched-date"
            assert page.locator("#sort").is_disabled()
            first_text = page.locator(".movie").first.inner_text()
            assert "Watched Jul 25, 2026" in first_text, first_text
            multi_text = page.locator('.movie[data-movie-id="multi"]').inner_text()
            assert "2 watches" in multi_text, multi_text
            opacities = page.locator(".movie").evaluate_all(
                "rows => rows.map(row => [getComputedStyle(row).opacity, getComputedStyle(row.querySelector('.movie-surface')).opacity])"
            )
            assert all(pair == ["1", "1"] for pair in opacities), opacities
            row_box = page.locator('.movie[data-movie-id="latest"]').bounding_box()
            rating_box = page.locator('.movie[data-movie-id="latest"] .rating-badge').bounding_box()
            # The rating sits at the row's end, left of the always-visible ⋯ menu (0.3.1).
            assert row_box and rating_box and row_box["x"] + row_box["width"] - (rating_box["x"] + rating_box["width"]) < 56
            assert page.locator('.movie[data-movie-id="latest"] .rating-badge').inner_text() == "IMDb 8.4"
            assert page.locator('.movie[data-movie-id="oldest"] .rating-badge').inner_text() == ""
            page.screenshot(path=str(SCREENSHOT), full_page=True)

            page.locator('.movie[data-movie-id="oldest"]').click()
            page.wait_for_selector(".back")
            assert page.locator(".rating-spotlight").count() == 0
            page.locator(".back").click()
            page.wait_for_selector('.movie[data-movie-id="latest"]')

            page.locator('.movie[data-movie-id="latest"]').click()
            page.wait_for_selector(".watch-history")
            spotlight_text = page.locator(".rating-spotlight").inner_text().splitlines()
            assert spotlight_text == ["IMDB USERS", "8.4", "/ 10 · 234.6K votes"], spotlight_text
            assert page.get_by_role("link", name="Open IMDb").get_attribute("href") == "https://www.imdb.com/title/tt1234567/"
            assert page.locator(".detail-notes h2").inner_text() == "Highlighted episodes"
            assert page.locator(".detail-notes > p").inner_text() == "Start with these episodes."
            highlights = page.locator(".detail-highlight")
            assert highlights.count() == 2
            assert highlights.nth(0).inner_text().splitlines() == ["Zima Blue", "S1E14 · 10 min"]
            assert highlights.nth(1).inner_text().splitlines() == ["Beyond the Aquila Rift", "S1E7 · 17 min"]
            assert page.locator(".detail-bump").count() == 0
            assert page.locator(".watch-event").count() == 1
            assert page.locator(".watch-event input").input_value() == "2026-07-25"
            assert page.locator(".detail-watch").inner_text() == "Watch again"

            page.locator(".watch-event input").fill("2026-04-30")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            saved_scroll = page.evaluate("window.scrollY")
            page.locator(".watch-date-save").click()
            page.wait_for_function("document.querySelector('.watch-event input')?.value === '2026-04-30'")
            page.wait_for_timeout(50)
            assert page.evaluate("document.activeElement?.classList.contains('watch-date-save')")
            assert abs(page.evaluate("window.scrollY") - saved_scroll) < 3
            page.locator(".back").click()
            ids = page.locator(".movie").evaluate_all("rows => rows.map(row => row.dataset.movieId)")
            assert ids == ["multi", "oldest", "latest"], ids

            page.locator('.movie[data-movie-id="latest"]').click()
            page.get_by_role("button", name="Watch again", exact=True).click()
            page.wait_for_function("document.querySelector('.detail-watch')?.textContent.includes('Mark watched again')")
            assert page.evaluate("document.activeElement?.classList.contains('detail-watch')")
            raw = json.loads(data.read_text())
            latest = next(movie for movie in raw if movie["id"] == "latest")
            assert latest["queued"] is True
            assert len(latest["watch_history"]) == 1

            page.locator(".back").click()
            page.get_by_role("button", name="Unwatched", exact=True).click()
            ids = page.locator(".movie").evaluate_all("rows => rows.map(row => row.dataset.movieId)")
            assert "latest" in ids and "queued" in ids, ids
            page.locator('.movie[data-movie-id="latest"]').click()
            assert page.locator(".watch-history").is_visible()
            assert page.locator(".detail-watch").inner_text() == "Mark watched"
            page.locator(".detail-new-watch-date").fill("2026-07-24")
            page.locator(".detail-watch").click()
            page.wait_for_function("document.querySelectorAll('.watch-event').length === 2")
            raw = json.loads(data.read_text())
            latest = next(movie for movie in raw if movie["id"] == "latest")
            assert latest["queued"] is False
            assert len(latest["watch_history"]) == 2

            page.locator(".back").click()
            page.get_by_role("button", name="Watched", exact=True).click()
            assert page.locator(".movie").first.get_attribute("data-movie-id") == "latest"
            assert "2 watches" in page.locator(".movie").first.inner_text()
            page.get_by_role("button", name="Poster view").click()
            tile_opacities = page.locator(".tile").evaluate_all("tiles => tiles.map(tile => getComputedStyle(tile).opacity)")
            assert tile_opacities and all(value == "1" for value in tile_opacities), tile_opacities

            # Explicit correction removes one event only after confirmation.
            page.locator('.tile[data-movie-id="multi"]').click()
            assert page.locator(".watch-event").count() == 2
            page.screenshot(path=str(DETAIL_SCREENSHOT), full_page=True)
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator(".watch-event").last.locator("[data-watch-delete]").click()
            page.wait_for_function("document.querySelectorAll('.watch-event').length === 1")
            page.wait_for_timeout(50)
            assert page.evaluate("document.activeElement?.hasAttribute('data-watch-delete')")
            raw = json.loads(data.read_text())
            multi = next(movie for movie in raw if movie["id"] == "multi")
            assert len(multi["watch_history"]) == 1

            # One real key press plus repeated keydown events records exactly one watch.
            page.locator(".back").click()
            page.get_by_role("button", name="Unwatched", exact=True).click()
            page.get_by_role("button", name="List view").click()
            queued_row = page.locator('.movie[data-movie-id="queued"]')
            queued_row.focus()
            queued_row.evaluate("""row => {
              row.dispatchEvent(new KeyboardEvent('keydown', {key:'w', code:'KeyW', bubbles:true}));
              for (let i = 0; i < 5; i += 1) {
                row.dispatchEvent(new KeyboardEvent('keydown', {key:'w', code:'KeyW', repeat:true, bubbles:true}));
              }
            }""")
            page.wait_for_function("!document.querySelector('.movie[data-movie-id=queued]')")
            raw = json.loads(data.read_text())
            queued = next(movie for movie in raw if movie["id"] == "queued")
            assert len(queued["watch_history"]) == 1
            assert not errors, errors
            browser.close()
        print("PASS: watched dates sort/edit, Watch Again preserves history, queue membership stays independent, and watched cards retain full contrast")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_watch_history_mobile_lifecycle()
