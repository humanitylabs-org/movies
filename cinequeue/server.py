from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from cinequeue.core import MovieStore, WatchStateConflict, parse_movie_requests
from cinequeue.watch_providers import WatchProviderError, WatchProviderService, configured_region, region_name

VERSION = "0.3.5"
ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web"
# Owner state lives in one writable directory: the packaged app mounts ${APP_DATA_DIR}/data at /data.
DATA_DIR = Path(os.environ.get("CINEQUEUE_DATA") or ROOT / "data")
DATA = DATA_DIR / "movies.json"
ADD_REQUESTS = DATA_DIR / "add_requests.json"
SWITCHES_FILE = DATA_DIR / "switches.json"
BASE_PATH = os.environ.get("CINEQUEUE_BASE_PATH", "/cinequeue").rstrip("/")
WATCH_PROVIDERS = WatchProviderService()
# Optional Wizard Agent Service integration for "find and fill in this film" requests.
#   disabled (default): requests are saved locally and reported as not sent; titles can still be added directly.
#   live: admitted exactly like Wizard's own client (session + run) at CINEQUEUE_WIZARD_ORIGIN.
#   stub / fail: QA-only fakes; never contact an agent.
ADMIT = os.environ.get("CINEQUEUE_ADMIT", "disabled").strip().lower()
WIZARD_ORIGIN = os.environ.get("CINEQUEUE_WIZARD_ORIGIN", "").strip().rstrip("/")
AGENT_NAME = os.environ.get("CINEQUEUE_AGENT_NAME", "").strip()[:40] or "your Wizard"


def configured_trust() -> tuple[set[str], set[str]]:
    """Read both the legacy Movies setting and Wizard's standard gateway trust settings."""
    origins = {
        value.strip().rstrip("/")
        for name in ("CINEQUEUE_ORIGINS", "WIZARD_TRUSTED_ORIGINS")
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    }
    hosts = {
        value.strip()
        for value in os.environ.get("WIZARD_TRUSTED_HOSTS", "").split(",")
        if value.strip()
    }
    hosts.update(filter(None, (urlsplit(origin).netloc for origin in origins)))
    return origins, hosts


ORIGINS, ORIGIN_HOSTS = configured_trust()
REQUEST_STATUSES = ("queued", "dispatched", "processing", "completed", "blocked", "failed")
# A request still "in flight" this long after its last update is treated as stalled and offered a retry.
STALE_REQUEST_SECONDS = 6 * 3600
MAX_REQUEST_CHOICES = 6
METADATA_FIELDS = {
    "title": str, "year": (int, type(None)), "poster": str, "letterboxd_url": str, "letterboxd_slug": str,
    "description": str, "genres": list, "directors": list, "creators": list, "cast": list, "trailer_url": str,
    "imdb_id": str, "imdb_url": str, "rating": (int, float, type(None)), "rating_count": (int, type(None)),
    "rating_source": str, "runtime_minutes": (int, type(None)), "media_type": str, "notes": str,
    "featured_episodes": list, "source": str,
}


class AddRequestDispatchError(RuntimeError):
    def __init__(self, message: str, record: dict):
        super().__init__(message)
        self.record = record


def strip_base_path(path: str) -> str:
    if BASE_PATH and path == BASE_PATH:
        return "/"
    if BASE_PATH and path.startswith(f"{BASE_PATH}/"):
        return path[len(BASE_PATH) :]
    return path


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clip(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def add_request_id(text: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{created_at}\n{text}".encode("utf-8")).hexdigest()[:10]
    stamp = reformat_timestamp_for_id(created_at)
    return f"cinequeue-add-{stamp}-{digest}"


def reformat_timestamp_for_id(timestamp: str) -> str:
    return timestamp.replace("-", "").replace(":", "").replace("+", "z").replace(".", "")[:15].lower()


def load_add_requests(path: Path | str = ADD_REQUESTS) -> list[dict]:
    request_path = Path(path)
    if not request_path.exists():
        return []
    try:
        data = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_add_requests(items: list[dict], path: Path | str = ADD_REQUESTS) -> None:
    request_path = Path(path)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = request_path.with_suffix(request_path.suffix + ".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(request_path)


@contextmanager
def lock_add_requests(path: Path | str = ADD_REQUESTS):
    request_path = Path(path)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = request_path.with_suffix(request_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# Owner feature switches (wizard.app-switches/1, wizard-os docs/APP_SWITCHES.md). Off by default; the state lives on
# the box in DATA_DIR/switches.json, so the app, the Tracker's Try it (through the App Manager) and the agent see one state.
SWITCHES = [
    {
        "id": "friendly-phone",
        "name": "Friendlier phone controls",
        "description": "Unwatched, Watched and Add move to a thumb bar at the bottom on phones (beside the tabs on wide "
        "windows), the row menu is always visible, Mark watched is readable, touch targets are 44 px and empty "
        "lists say what to do.",
        "default": False,
        "issues": ["humanitylabs-org/movies#14"],
    },
]


def _saved_switches(path: Path | str | None = None) -> dict:
    try:
        saved = json.loads(Path(path or SWITCHES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def switches_doc(path: Path | str | None = None) -> dict:
    saved = _saved_switches(path)
    return {
        "contract": "wizard.app-switches/1",
        "switches": [
            dict(s, on=saved[s["id"]] if isinstance(saved.get(s["id"]), bool) else s["default"]) for s in SWITCHES
        ],
    }


def set_switch(switch_id: str, on: bool, path: Path | str | None = None) -> dict:
    target = Path(path or SWITCHES_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    known = {s["id"] for s in SWITCHES}
    with lock_add_requests(target):
        saved = {k: v for k, v in _saved_switches(target).items() if k in known and isinstance(v, bool)}
        saved[switch_id] = bool(on)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        tmp.replace(target)
    return switches_doc(target)


def prune_switches(path: Path | str | None = None) -> None:
    """Drop saved values for switches that no longer exist (a removed feature leaves nothing on the box)."""
    target = Path(path or SWITCHES_FILE)
    saved = _saved_switches(target)
    known = {s["id"] for s in SWITCHES}
    if not saved or set(saved) <= known:
        return
    with lock_add_requests(target):
        # movies#21: read strictly under the lock. If the file cannot be read or parsed now, leave it exactly as it
        # is; publishing the `{}` fallback here would wipe the owner's surviving choices.
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(current, dict):
            return
        kept = {k: v for k, v in current.items() if k in known and isinstance(v, bool)}
        if kept == current:
            return
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        tmp.replace(target)


def build_cinequeue_add_prompt(text: str) -> str:
    requested = clip(text, 800)
    return f"""Add this to the owner's Movies: {requested}

Use the Movies app's own MCP tools (cinequeue_*) or its documented HTTP API, not app files, the container or the bare quick-add shortcut alone. Resolve exact-title candidates first (Letterboxd or another reliable film source); if multiple years/titles plausibly match, mark the request blocked before writing: `detail` is one short question (no URLs) and `choices` lists 2-6 short tappable answers such as "Metropolis (1927) · Fritz Lang"; the owner taps one in the app. Once the intended film/series is clear, add it with cinequeue_add and enrich the card with cinequeue_update: poster, Letterboxd URL/slug, IMDb ID/URL, year, description, genres, directors/creators, cast, trailer, runtime when available, and source. When the owner names specific episodes or other viewing priorities, keep the synopsis clean, put the human note in `notes`, and store structured episode entries in `featured_episodes` so the detail view can highlight them. Only set `rating`, `rating_count` and `rating_source: imdb` from IMDb's official `title.ratings.tsv.gz` dataset; never hand-copy or relabel a Letterboxd score. Replace any bare placeholder instead of duplicating it. Verify with cinequeue_get. Record the request lifecycle with cinequeue_request_update: `processing` when work starts, `completed` only after verification, or `blocked`/`failed` with a concise detail when work cannot finish."""


def queue_add_request(text: str, *, request_log_path: Path | str = ADD_REQUESTS) -> dict:
    requested = clip(text, 800)
    if not requested:
        raise ValueError("request text is required")
    created_at = utc_now()
    record = {
        "id": add_request_id(requested, created_at),
        "text": requested,
        "status": "queued",
        "created_at": created_at,
        "updated_at": created_at,
        "source_app": "Movies",
        "agent": AGENT_NAME,
        "prompt": build_cinequeue_add_prompt(requested),
    }
    with lock_add_requests(request_log_path):
        items = load_add_requests(request_log_path)
        items.insert(0, record)
        save_add_requests(items[:200], request_log_path)
    return record


def update_add_request(
    request_id: str,
    status: str | None,
    *,
    detail: str = "",
    request_log_path: Path | str = ADD_REQUESTS,
    extra: dict | None = None,
    expected_status: set[str] | None = None,
    choices: list | None = None,
) -> dict:
    with lock_add_requests(request_log_path):
        items = load_add_requests(request_log_path)
        for item in items:
            if item.get("id") != request_id:
                continue
            if status is not None and (expected_status is None or item.get("status") in expected_status):
                item["status"] = clip(status, 40)
            item["updated_at"] = utc_now()
            if detail:
                item["status_detail"] = clip(detail, 800)
            if choices is not None:
                item["choices"] = clean_choices(choices)
            if extra:
                item.update(extra)
            save_add_requests(items, request_log_path)
            return item
    raise KeyError(f"request not found: {request_id}")


class AdmissionError(RuntimeError):
    pass


def agent_ready(mode: str | None = None) -> bool:
    mode = mode or ADMIT
    return mode in {"stub", "fail"} or (mode == "live" and bool(WIZARD_ORIGIN))


def _wizard(method: str, path: str, body: dict, headers: dict | None = None, timeout: float = 10) -> dict:
    request = urllib.request.Request(
        WIZARD_ORIGIN + path,
        method=method,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise AdmissionError(f"Wizard answered HTTP {exc.code} on {path.rsplit('/', 1)[-1]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdmissionError(f"Wizard could not be reached ({getattr(exc, 'reason', exc)})") from exc
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


def dispatch_add_request(record: dict, *, mode: str | None = None) -> dict:
    """Hand one saved request to the Wizard Agent Service (the only sanctioned agent route)."""
    request_id = str(record.get("id") or "")
    if not request_id:
        raise AdmissionError("Movies add request has no id")
    mode = mode or ADMIT
    session_id = f"wizard_{int(time.time() * 1000)}_{secrets.token_hex(6)}"
    if mode == "stub":
        return {"status": "accepted", "session_id": "stub_" + session_id, "run_id": "stub_run", "mode": "stub"}
    if mode == "fail":
        raise AdmissionError("Wizard could not be reached (stubbed outage)")
    if mode != "live" or not WIZARD_ORIGIN:
        raise AdmissionError("Agent requests are not configured. Your request is saved; add the title directly or enable the optional Agent Service integration.")
    prompt = f"{record.get('prompt') or build_cinequeue_add_prompt(record.get('text', ''))}\n\nMovies request ID: {request_id}"
    target = os.environ.get("CINEQUEUE_AGENT_API_URL", "").rstrip("/")
    if target:
        prompt += f"\nThis request belongs ONLY to the Movies instance at {target}. Use its documented HTTP API or {target}/mcp. Do not modify a different queue. Read {target}/openapi.json first."
    _wizard("POST", "/wizard/hermes/api/sessions", {"source": "wizard-mobile", "id": session_id})
    run = _wizard(
        "POST",
        "/wizard/hermes/v1/runs",
        {"session_id": session_id, "input": prompt},
        {"Idempotency-Key": str(uuid.uuid4())},
        timeout=20,
    )
    return {"status": "accepted", "session_id": session_id, "run_id": str(run.get("run_id") or run.get("id") or ""), "mode": "live"}


def queue_and_dispatch_add_request(
    text: str,
    *,
    request_log_path: Path | str = ADD_REQUESTS,
    dispatcher=dispatch_add_request,
) -> dict:
    record = queue_add_request(text, request_log_path=request_log_path)
    return dispatch_saved_request(record, request_log_path=request_log_path, dispatcher=dispatcher)


def dispatch_saved_request(
    record: dict,
    *,
    request_log_path: Path | str = ADD_REQUESTS,
    dispatcher=dispatch_add_request,
) -> dict:
    try:
        response = dispatcher(dict(record))
    except Exception as exc:
        status = "not_sent" if not agent_ready() and dispatcher is dispatch_add_request else "dispatch_failed"
        failed = update_add_request(
            record["id"],
            status,
            detail=str(exc),
            request_log_path=request_log_path,
            extra={"dispatch_error": clip(exc, 800)},
        )
        raise AddRequestDispatchError(str(exc), failed) from exc
    return update_add_request(
        record["id"],
        "dispatched",
        request_log_path=request_log_path,
        extra={"dispatched_at": utc_now(), "dispatch_response": response},
        expected_status={"queued", "not_sent", "dispatch_failed"},
    )


def clean_choices(choices: object) -> list[str]:
    if not isinstance(choices, list):
        return []
    out: list[str] = []
    for choice in choices:
        text = clip(choice, 120)
        if text and text not in out:
            out.append(text)
    return out[:MAX_REQUEST_CHOICES]


def _age_seconds(timestamp: object) -> float:
    try:
        moment = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - moment).total_seconds()


def request_is_stalled(record: dict) -> bool:
    return record.get("status") in {"queued", "dispatched", "processing"} and _age_seconds(record.get("updated_at")) > STALE_REQUEST_SECONDS


def retry_add_request(request_id: str, *, request_log_path: Path | str = ADD_REQUESTS, dispatcher=dispatch_add_request) -> dict:
    record = next((item for item in load_add_requests(request_log_path) if item.get("id") == request_id), None)
    if record is None:
        raise KeyError(request_id)
    retryable = record.get("status") in {"not_sent", "dispatch_failed", "failed"} or request_is_stalled(record)
    if not retryable:
        return record
    # Re-open the record so dispatch can move it forward again.
    reopened = update_add_request(request_id, "queued", request_log_path=request_log_path, extra={"status_detail": ""})
    return dispatch_saved_request(reopened, request_log_path=request_log_path, dispatcher=dispatcher)


def answer_add_request(
    request_id: str,
    answer: str,
    *,
    request_log_path: Path | str = ADD_REQUESTS,
    dispatcher=dispatch_add_request,
) -> dict:
    """The owner answered a clarification: send the original request back with their choice."""
    answer = clip(answer, 200)
    if not answer:
        raise ValueError("answer is required")
    record = next((item for item in load_add_requests(request_log_path) if item.get("id") == request_id), None)
    if record is None:
        raise KeyError(request_id)
    if record.get("status") != "blocked":
        return record
    question = clip(record.get("status_detail"), 400)
    prompt = build_cinequeue_add_prompt(record.get("text", "")) + (
        f"\n\nThis request was blocked for clarification ({question}). The owner has now chosen: {answer}. "
        "Add exactly that title (or update it if it is already in the list); do not ask again."
    )
    reopened = update_add_request(
        request_id,
        "queued",
        request_log_path=request_log_path,
        extra={"answer": answer, "answered_at": utc_now(), "prompt": prompt, "status_detail": "", "choices": []},
    )
    return dispatch_saved_request(reopened, request_log_path=request_log_path, dispatcher=dispatcher)


def dismiss_add_request(request_id: str, *, request_log_path: Path | str = ADD_REQUESTS) -> dict:
    return update_add_request(request_id, "dismissed", request_log_path=request_log_path, extra={"dismissed_at": utc_now()})


def public_config() -> dict:
    region = configured_region()
    return {
        "version": VERSION,
        "agent": {"ready": agent_ready(), "name": AGENT_NAME, "mode": ADMIT if agent_ready() else "disabled"},
        "watch": {"enabled": bool(region), "region": region, "region_name": region_name(region) if region else ""},
    }


def health() -> tuple[bool, dict]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".healthz"
        probe.write_text("ok")
        probe.unlink()
        movies = MovieStore(DATA).all()
        if not isinstance(movies, list):
            raise ValueError("movie store is not a list")
        return True, {"ok": True, "version": VERSION, "movies": len(movies)}
    except Exception as exc:  # health must report, not raise
        return False, {"ok": False, "version": VERSION, "error": type(exc).__name__}


def apply_metadata(movie_id: str, fields: dict) -> dict:
    """Enrich one card with known metadata fields (agent/MCP path); unknown fields are rejected."""
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields are required")
    unknown = set(fields) - set(METADATA_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    for key, value in fields.items():
        if not isinstance(value, METADATA_FIELDS[key]) or isinstance(value, bool):
            raise ValueError(f"invalid value for {key}")
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError(f"{key} is too long")
        if isinstance(value, list) and len(value) > 60:
            raise ValueError(f"{key} has too many items")
    if "title" in fields and not fields["title"].strip():
        raise ValueError("title cannot be empty")
    return MovieStore(DATA).update_metadata(movie_id, fields)


def watch_history_feed(limit: int = 100) -> list[dict]:
    events = []
    for movie in MovieStore(DATA).all_projected():
        for event in movie.get("watch_history") or []:
            events.append({"movie_id": movie.get("id"), "title": movie.get("title"), "year": movie.get("year"),
                           "event_id": event.get("id"), "watched_on": event.get("watched_on"),
                           "recorded_at": event.get("recorded_at")})
    events.sort(key=lambda e: (e.get("watched_on") or "0000-00-00", e.get("recorded_at") or ""), reverse=True)
    return events[: max(1, min(int(limit), 1000))]


class CineQueueHandler(SimpleHTTPRequestHandler):
    server_version = "Movies/0.2"

    @property
    def store(self) -> MovieStore:
        return MovieStore(DATA)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - no request bodies, keys or titles in logs
        sys.stderr.write("%s %s\n" % (self.command, (self.path or "").split("?", 1)[0][:120]))

    def do_GET(self) -> None:
        if not self._host_ok()[0]:
            return self._json({"error": "unknown host; open Movies through your Wizard"}, HTTPStatus.MISDIRECTED_REQUEST)
        parsed = urlparse(self.path)
        if BASE_PATH and parsed.path == BASE_PATH:
            return self._redirect_to_trailing_slash(parsed)
        clean_path = strip_base_path(parsed.path)
        if clean_path in ("/healthz", "/api/health"):
            ok, payload = health()
            return self._json(payload, HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE)
        if clean_path == "/wizard-os-home/core/app-client.js":
            origin = os.environ.get("CINEQUEUE_SHARED_ORIGIN", "") or WIZARD_ORIGIN
            if not origin:
                return self._json({"error": "Open Movies through Wizard, or configure CINEQUEUE_SHARED_ORIGIN for standalone access"}, HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                request = urllib.request.Request(origin.rstrip("/") + "/wizard-os-home/core/app-client.js")
                with urllib.request.urlopen(request, timeout=5) as response:
                    content = response.read(256 * 1024)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return None
            except Exception:
                return self._json({"error": "Wizard shared interface is temporarily unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
        if clean_path == "/api/config":
            return self._json(public_config())
        if clean_path == "/api/switches":
            if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
                return self._json({"error": "Only this Wizard's owner can see Movies switches."}, HTTPStatus.FORBIDDEN)
            return self._json(switches_doc())
        if clean_path == "/api/history":
            limit = parse_qs(parsed.query).get("limit", ["100"])[0]
            return self._json({"history": watch_history_feed(int(limit) if limit.isdigit() else 100)})
        if clean_path == "/openapi.json":
            return self._file(ROOT / "openapi.json", "application/json")
        if clean_path == "/mcp":
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if clean_path == "/api/movies":
            include_archived = parse_qs(parsed.query).get("include_archived", [""])[0] == "1"
            movies = self.store.all_projected()
            if not include_archived:
                movies = [m for m in movies if not m.get("archived")]
            return self._json({"movies": movies})
        if clean_path == "/api/add-requests":
            return self._json({"requests": load_add_requests(ADD_REQUESTS)})
        if clean_path == "/api/genres":
            genres = sorted({genre for movie in self.store.all() for genre in movie.get("genres", [])})
            return self._json({"genres": genres})
        if clean_path.startswith("/api/watch-providers/"):
            movie_id = unquote(clean_path.removeprefix("/api/watch-providers/"))
            movie = self.store.get_projected(movie_id)
            if not movie:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
            region = configured_region()
            if not region:
                return self._json({"enabled": False, "error": "Where-to-watch lookup is not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                return self._json(WATCH_PROVIDERS.lookup(movie, region=region))
            except WatchProviderError as exc:
                print(f"watch-provider lookup failed: {type(exc).__name__}", file=sys.stderr)
                return self._json(
                    {"error": f"{region_name(region)} availability is temporarily unavailable"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
        if clean_path.startswith("/api/movie/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            movie = self.store.get_projected(movie_id)
            if movie:
                return self._json({"movie": movie})
            return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path in ("/", "") or clean_path.startswith("/movie/"):
            self.path = "/index.html"
        elif clean_path != parsed.path:
            self.path = clean_path
        return super().do_GET()

    def _host_ok(self) -> tuple[bool, bool, str]:
        """DNS-rebinding guard for reads and writes: Host must be a configured Wizard origin host, or
        box-local (loopback on any published port, or the container's service name)."""
        host = (self.headers.get("Host") or "").strip()
        name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        local = name.lower() in {"127.0.0.1", "localhost", "[::1]", "cinequeue"}
        return (host in ORIGIN_HOSTS or local), local, host

    def _write_allowed(self) -> bool:
        """Known Host, and a browser Origin that is absent, configured, or this box-local host itself."""
        if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
            return False
        ok, local, host = self._host_ok()
        if not ok:
            return False
        origin = (self.headers.get("Origin") or "").rstrip("/")
        return not origin or origin in ORIGINS or (local and origin == f"http://{host}")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        clean_path = strip_base_path(parsed.path)
        if not self._write_allowed():
            return self._json({"error": "origin not allowed"}, HTTPStatus.FORBIDDEN)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 262144:
            return self._json({"error": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        # Browsers (which send Origin) must use JSON so a cross-site "simple" form post cannot write.
        is_json = self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"
        if length and self.headers.get("Origin") and not is_json:
            return self._json({"error": "application/json required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            return self._post(clean_path)
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)

    def _post(self, clean_path: str) -> None:
        if clean_path == "/api/switches":
            return self._post_switch()
        if clean_path == "/mcp":
            from cinequeue import mcp_api

            status, body = mcp_api.dispatch(self._read_json())
            if status == 202:
                self.send_response(HTTPStatus.ACCEPTED)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._json(body, HTTPStatus(status))
        if clean_path.startswith("/api/update/"):
            movie_id = unquote(clean_path.removeprefix("/api/update/"))
            try:
                return self._json({"movie": apply_metadata(movie_id, self._read_json().get("fields") or {})})
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/add-requests/"):
            request_id = unquote(clean_path.removeprefix("/api/add-requests/"))
            payload = self._read_json()
            status = str(payload.get("status") or "")
            try:
                if status == "retry":
                    record = retry_add_request(request_id, request_log_path=ADD_REQUESTS)
                elif status == "answer":
                    record = answer_add_request(request_id, str(payload.get("answer") or ""), request_log_path=ADD_REQUESTS)
                elif status == "dismiss":
                    record = dismiss_add_request(request_id, request_log_path=ADD_REQUESTS)
                elif status in REQUEST_STATUSES:
                    record = update_add_request(
                        request_id,
                        status,
                        detail=str(payload.get("detail") or ""),
                        request_log_path=ADD_REQUESTS,
                        choices=payload.get("choices") if "choices" in payload else None,
                    )
                else:
                    return self._json({"error": "status must be retry, answer, dismiss or one of " + ", ".join(REQUEST_STATUSES)}, HTTPStatus.BAD_REQUEST)
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except KeyError:
                return self._json({"error": "request not found"}, HTTPStatus.NOT_FOUND)
            except AddRequestDispatchError as exc:
                return self._json({"ok": False, "error": str(exc), "request": exc.record}, HTTPStatus.SERVICE_UNAVAILABLE)
            return self._json({"ok": True, "request": record})
        if clean_path == "/api/add":
            payload = self._read_json()
            text = str(payload.get("text") or payload.get("title") or "")
            movies = parse_movie_requests(text)
            added = self.store.add_many(movies)
            return self._json({"added": added, "count": len(added)})
        if clean_path == "/api/request-add":
            payload = self._read_json()
            text = str(payload.get("text") or payload.get("title") or payload.get("request") or "")
            try:
                record = queue_and_dispatch_add_request(text, request_log_path=ADD_REQUESTS)
            except ValueError as exc:
                return self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except AddRequestDispatchError as exc:
                return self._json(
                    {"ok": False, "error": str(exc), "request": exc.record},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return self._json({"ok": True, "request": record}, HTTPStatus.ACCEPTED)
        if clean_path.startswith("/api/toggle/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            try:
                return self._json({"movie": self.store.toggle_watched(movie_id)})
            except WatchStateConflict as exc:
                return self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/watch-again/"):
            movie_id = unquote(clean_path.removeprefix("/api/watch-again/"))
            try:
                return self._json({"movie": self.store.queue_again(movie_id)})
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/watch-date/"):
            remainder = clean_path.removeprefix("/api/watch-date/")
            try:
                raw_movie_id, raw_event_id = remainder.split("/", 1)
            except ValueError:
                return self._json({"error": "watch event not found"}, HTTPStatus.NOT_FOUND)
            payload = self._read_json()
            try:
                movie = self.store.edit_watch_date(
                    unquote(raw_movie_id),
                    unquote(raw_event_id),
                    str(payload.get("watched_on") or ""),
                )
                return self._json({"movie": movie})
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except KeyError:
                return self._json({"error": "watch event not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/watch-delete/"):
            remainder = clean_path.removeprefix("/api/watch-delete/")
            try:
                raw_movie_id, raw_event_id = remainder.split("/", 1)
            except ValueError:
                return self._json({"error": "watch event not found"}, HTTPStatus.NOT_FOUND)
            payload = self._read_json()
            try:
                movie = self.store.delete_watch_event(
                    unquote(raw_movie_id),
                    unquote(raw_event_id),
                    requeue=bool(payload.get("requeue")),
                )
                return self._json({"movie": movie})
            except KeyError:
                return self._json({"error": "watch event not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/watch/"):
            movie_id = unquote(clean_path.removeprefix("/api/watch/"))
            payload = self._read_json()
            try:
                movie = self.store.mark_watched(movie_id, str(payload.get("watched_on") or "") or None)
                return self._json({"movie": movie})
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/bump/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            try:
                return self._json({"movie": self.store.move_to_top(movie_id)})
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/archive/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            try:
                return self._json({"movie": self.store.set_archived(movie_id, True)})
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/restore/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            try:
                return self._json({"movie": self.store.set_archived(movie_id, False)})
            except KeyError:
                return self._json({"error": "movie not found"}, HTTPStatus.NOT_FOUND)
        if clean_path.startswith("/api/delete/"):
            movie_id = unquote(clean_path.rsplit("/", 1)[-1])
            self.store.delete(movie_id)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _post_switch(self) -> None:
        # Owner only (same Host/Origin trust as every Movies write), with X-Wizard-Switch: 1 and a JSON body.
        if self.headers.get("X-Wizard-Switch") != "1":
            return self._json({"error": "Only this Wizard's owner can switch Movies."}, HTTPStatus.FORBIDDEN)
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._json({"error": "application/json required"}, HTTPStatus.BAD_REQUEST)
        body = self._read_json()
        if not isinstance(body.get("id"), str) or not isinstance(body.get("on"), bool):
            return self._json({"error": "body must be {\"id\": \"...\", \"on\": true|false}"}, HTTPStatus.BAD_REQUEST)
        if body["id"] not in {s["id"] for s in SWITCHES}:
            return self._json({"error": "No such switch"}, HTTPStatus.NOT_FOUND)
        return self._json(set_switch(body["id"], body["on"]))

    def _redirect_to_trailing_slash(self, parsed) -> None:
        target = f"{BASE_PATH}/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        self.send_response(HTTPStatus.MOVED_PERMANENTLY)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self) -> dict:
        if hasattr(self, "_body_cache"):
            return self._body_cache
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        self._body_cache = data if isinstance(data, dict) else {}
        return self._body_cache

    def _file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(port: int = 8765, host: str = "127.0.0.1") -> None:
    handler = lambda *args, **kwargs: CineQueueHandler(*args, directory=str(STATIC), **kwargs)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from cinequeue.starter import initialize
    initialize(DATA)  # public seed only when the owner store has never existed
    prune_switches()
    server = ThreadingHTTPServer((host, port), handler)
    public_path = BASE_PATH or "/"
    print(f"Movies running at http://{host}:{port}{public_path}")
    server.serve_forever()


if __name__ == "__main__":
    run(
        port=int(os.environ.get("CINEQUEUE_PORT", "8765")),
        host=os.environ.get("CINEQUEUE_HOST", "127.0.0.1"),
    )
