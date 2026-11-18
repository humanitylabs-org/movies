"""Stateless Streamable HTTP MCP for CineQueue, using the same store operations as the view and API."""
from __future__ import annotations

import json

from cinequeue import server
from cinequeue.core import MovieStore, WatchStateConflict, parse_movie_requests

S = {"type": "string"}
I = {"type": "integer"}


def schema(props: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS = [
    {"name": "cinequeue_list", "description": "List the queue in order (default), watched history order, archived or all titles. Optional case-insensitive search on title/director/genre.",
     "inputSchema": schema({"view": {"type": "string", "enum": ["queue", "watched", "archived", "all"]}, "search": S, "limit": I})},
    {"name": "cinequeue_get", "description": "Read one title card with its queue position and watch history.", "inputSchema": schema({"id": S}, ("id",))},
    {"name": "cinequeue_add", "description": "Add titles directly to the top of the queue. Either text ('Stalker 1979, Heat') or one title with optional year. Duplicates are not re-added; when the owner's bump-existing switch is on, a title already in the list moves to the top instead (watched ones are queued again, archived ones restored) and is returned in `bumped`.",
     "inputSchema": schema({"text": S, "title": S, "year": I})},
    {"name": "cinequeue_update", "description": "Enrich a card's metadata (poster, description, genres, directors, cast, trailer_url, imdb_id, imdb_url, rating/rating_count/rating_source=imdb, runtime_minutes, notes, featured_episodes, letterboxd_url/slug, media_type, title, year, source). Queue order and watch history are unchanged.",
     "inputSchema": schema({"id": S, "fields": {"type": "object"}}, ("id", "fields"))},
    {"name": "cinequeue_bump", "description": "Move a title to the top of the queue.", "inputSchema": schema({"id": S}, ("id",))},
    {"name": "cinequeue_mark_watched", "description": "Record a watch (optional watched_on YYYY-MM-DD, default today) and take it off the queue; history is kept.",
     "inputSchema": schema({"id": S, "watched_on": S}, ("id",))},
    {"name": "cinequeue_watch_again", "description": "Put a watched title back at the top of the queue, keeping its watch history.", "inputSchema": schema({"id": S}, ("id",))},
    {"name": "cinequeue_history", "description": "Watch history across all titles, newest first.", "inputSchema": schema({"limit": I})},
    {"name": "cinequeue_archive", "description": "Archive (hide) or restore a title. Nothing is deleted.", "inputSchema": schema({"id": S, "archived": {"type": "boolean"}}, ("id", "archived"))},
    {"name": "cinequeue_request", "description": "Save a natural-language 'find and add this' request; it is sent to the Agent Service only when the operator enabled that integration. Returns sent=false honestly otherwise.",
     "inputSchema": schema({"text": S}, ("text",))},
    {"name": "cinequeue_requests", "description": "List saved add requests and their lifecycle status.", "inputSchema": schema({})},
    {"name": "cinequeue_request_update", "description": "Record an add request's lifecycle: processing, completed (only after verification), blocked or failed. For blocked, detail is one short question (no URLs) and choices lists 2-6 short answers the owner can tap; their pick is sent back to you automatically.",
     "inputSchema": schema({"id": S, "status": {"type": "string", "enum": ["processing", "completed", "blocked", "failed"]}, "detail": S, "choices": {"type": "array", "items": S}}, ("id", "status"))},
]


def _store() -> MovieStore:
    return MovieStore(server.DATA)


def _with_position(movie: dict, movies: list[dict]) -> dict:
    queue = [m["id"] for m in movies if m.get("queued") and not m.get("archived")]
    movie = dict(movie)
    movie["queue_position"] = queue.index(movie["id"]) + 1 if movie.get("id") in queue else None
    return movie


def _summary(movie: dict) -> dict:
    keys = ("id", "title", "year", "media_type", "genres", "directors", "runtime_minutes", "rating", "rating_source",
            "queued", "watched", "watch_count", "latest_watched_on", "archived", "notes", "poster")
    return {k: movie.get(k) for k in keys if movie.get(k) not in (None, "", [])}


def _list(args: dict) -> dict:
    view = args.get("view") or "queue"
    movies = _store().all_projected()
    if view == "queue":
        rows = [m for m in movies if m.get("queued") and not m.get("archived")]
    elif view == "watched":
        rows = sorted((m for m in movies if m.get("watched") and not m.get("archived")),
                      key=lambda m: m.get("latest_watched_on") or "", reverse=True)
    elif view == "archived":
        rows = [m for m in movies if m.get("archived")]
    else:
        rows = movies
    needle = str(args.get("search") or "").strip().casefold()
    if needle:
        def hay(m):
            return " ".join([str(m.get("title") or ""), *map(str, m.get("directors") or []), *map(str, m.get("genres") or [])]).casefold()
        rows = [m for m in rows if needle in hay(m)]
    limit = max(1, min(int(args.get("limit") or 200), 1000))
    out = []
    for index, movie in enumerate(rows[:limit], start=1):
        item = _summary(movie)
        if view == "queue":
            item["queue_position"] = index
        out.append(item)
    return {"view": view, "total": len(rows), "movies": out}


def call(name: str, a: dict) -> dict:
    store = _store()
    if name == "cinequeue_list":
        return _list(a)
    if name == "cinequeue_get":
        movie = store.get_projected(a["id"])
        if not movie:
            raise KeyError(a["id"])
        return {"movie": _with_position(movie, store.all_projected())}
    if name == "cinequeue_add":
        if a.get("title"):
            year = a.get("year")
            items = [{"title": str(a["title"]).strip()[:300], "year": year if isinstance(year, int) else None}]
        else:
            items = parse_movie_requests(str(a.get("text") or ""))
        if not items:
            raise ValueError("text or title is required")
        result = store.add_or_bump(items, bump_existing=server.switch_on("bump-existing"))
        out = {"added": [store.project(m) for m in result["added"]], "count": len(result["added"])}
        if result["bumped"]:
            # movies#23: titles already in the list moved to the top (watched ones queued again, archived restored).
            out["bumped"] = result["bumped"]
            out["note"] = "Already in the list, now first: " + ", ".join(m.get("title", "") for m in result["bumped"])
        return out
    if name == "cinequeue_update":
        return {"movie": server.apply_metadata(a["id"], a["fields"])}
    if name == "cinequeue_bump":
        return {"movie": store.move_to_top(a["id"])}
    if name == "cinequeue_mark_watched":
        return {"movie": store.mark_watched(a["id"], a.get("watched_on") or None)}
    if name == "cinequeue_watch_again":
        return {"movie": store.queue_again(a["id"])}
    if name == "cinequeue_history":
        return {"history": server.watch_history_feed(int(a.get("limit") or 100))}
    if name == "cinequeue_archive":
        return {"movie": store.set_archived(a["id"], bool(a["archived"]))}
    if name == "cinequeue_request":
        try:
            record = server.queue_and_dispatch_add_request(str(a["text"]), request_log_path=server.ADD_REQUESTS)
            return {"sent": True, "request": record}
        except server.AddRequestDispatchError as exc:
            return {"sent": False, "saved": True, "error": str(exc), "request": exc.record}
    if name == "cinequeue_requests":
        return {"requests": [{k: v for k, v in r.items() if k != "prompt"} for r in server.load_add_requests(server.ADD_REQUESTS)]}
    if name == "cinequeue_request_update":
        return {"request": server.update_add_request(a["id"], a["status"], detail=str(a.get("detail") or ""), request_log_path=server.ADD_REQUESTS, choices=a.get("choices") if "choices" in a else None)}
    raise ValueError("Unknown tool")


def dispatch(d: dict) -> tuple[int, dict]:
    key = d.get("id") if isinstance(d, dict) else None
    if not isinstance(d, dict) or d.get("jsonrpc") != "2.0" or not isinstance(d.get("method"), str):
        return 200, {"jsonrpc": "2.0", "id": key, "error": {"code": -32600, "message": "Invalid request"}}
    method = d["method"]
    if key is None and method.startswith("notifications/"):
        return 202, {}
    out: dict = {"jsonrpc": "2.0", "id": key}
    if method == "initialize":
        out["result"] = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                         "serverInfo": {"name": "cinequeue", "version": server.VERSION}}
    elif method == "ping":
        out["result"] = {}
    elif method == "tools/list":
        out["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        params = d.get("params") or {}
        if not isinstance(params, dict):
            params = {"arguments": None}
        args = params.get("arguments", {})
        name = str(params.get("name") or "")
        tool = next((t for t in TOOLS if t["name"] == name), None)
        try:
            if (not tool or not isinstance(args, dict) or set(args) - set(tool["inputSchema"]["properties"])
                    or set(tool["inputSchema"]["required"]) - set(args)):
                raise ValueError("Unknown tool or invalid arguments")
            result = call(name, args)
            out["result"] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}
        except KeyError as exc:
            out["result"] = {"content": [{"type": "text", "text": f"not found: {exc.args[0] if exc.args else ''}"}], "isError": True}
        except (ValueError, TypeError, WatchStateConflict) as exc:
            out["result"] = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        out["error"] = {"code": -32601, "message": "Method not found"}
    return 200, out
