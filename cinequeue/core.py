from __future__ import annotations

import fcntl
import json
import hashlib
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_STORE_LOCKS: dict[Path, "_StoreLock"] = {}
_STORE_LOCKS_GUARD = threading.Lock()
try:
    WATCH_TIMEZONE = ZoneInfo(os.environ.get("CINEQUEUE_TIMEZONE", "UTC"))
except ZoneInfoNotFoundError:
    WATCH_TIMEZONE = timezone.utc


class WatchStateConflict(ValueError):
    """A legacy toggle cannot preserve the current watch-history contract."""


_DROP_PREFIX = re.compile(
    r"^(?:add|watch|i want to watch|i'd like to watch|please add|put|track|movie|movies|film|films|to my watchlist|to watchlist|and)\s+",
    re.I,
)
_YEAR_PAREN = re.compile(r"^(.+?)\s*\((\d{4})\)$")
_YEAR_TRAIL = re.compile(r"^(.+?)\s+(\d{4})$")


def slugify(title: str, year: int | None = None) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return f"{slug}-{year}" if year else slug


def _clean_piece(piece: str) -> str:
    piece = piece.strip(" \t\n\r.,;:!?")
    changed = True
    while changed:
        new = _DROP_PREFIX.sub("", piece).strip(" \t\n\r.,;:!?")
        changed = new != piece
        piece = new
    piece = re.sub(r"\s+(?:to my watchlist|to watch|please)$", "", piece, flags=re.I).strip()
    return piece


def parse_movie_requests(text: str) -> list[dict[str, Any]]:
    """Parse a casual sentence into movie title/year records.

    Handles comma-separated titles plus a final "and" item. Years may be in
    parentheses or trailing, e.g. "Stalker 1979".
    """
    text = text.replace("\n", ",")
    text = re.sub(r"\s+and\s+", ",", text, flags=re.I)
    parts = [_clean_piece(p) for p in text.split(",")]
    movies: list[dict[str, Any]] = []
    for part in parts:
        if not part:
            continue
        year = None
        match = _YEAR_PAREN.match(part) or _YEAR_TRAIL.match(part)
        if match:
            part = match.group(1).strip()
            year = int(match.group(2))
        if len(part) < 2:
            continue
        movies.append({"title": part, "year": year})
    return movies


def normalize_watch_date(value: object) -> str:
    """Return an ISO calendar date or raise for a non-empty invalid value."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if len(text) > 10:
            parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed_datetime.tzinfo is not None:
                parsed_datetime = parsed_datetime.astimezone(WATCH_TIMEZONE)
            parsed = parsed_datetime.date()
        else:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("watched_on must be YYYY-MM-DD") from exc
    return parsed.isoformat()


class _StoreLock:
    """Re-entrant in-process lock plus an advisory flock on `<store>.lock` for other processes.

    Writes stay atomic (temp file + os.replace); the flock lets out-of-process readers such as
    the private migration snapshot take a consistent copy without racing a read-modify-write.
    """

    def __init__(self, path: Path):
        self._rlock = threading.RLock()
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._local = threading.local()

    def __enter__(self):
        self._rlock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            try:
                handle = self._lock_path.open("a+", encoding="utf-8")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._local.handle = handle
            except OSError:
                self._local.handle = None
        self._local.depth = depth + 1
        return self

    def __exit__(self, *exc):
        self._local.depth -= 1
        if self._local.depth == 0 and getattr(self._local, "handle", None) is not None:
            try:
                fcntl.flock(self._local.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._local.handle.close()
                self._local.handle = None
        self._rlock.release()
        return False


def _store_lock(path: Path) -> _StoreLock:
    resolved = path.resolve()
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(resolved, _StoreLock(resolved))


def _watch_event_id(movie_id: str, watched_on: str, recorded_at: str) -> str:
    digest = hashlib.sha256(f"{movie_id}\n{watched_on}\n{recorded_at}".encode("utf-8")).hexdigest()[:16]
    return f"watch-{digest}"


class MovieStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = _store_lock(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.path.exists():
                self.path.write_text("[]")

    def all(self) -> list[dict[str, Any]]:
        """Return movies in their stored order.

        The JSON file order is meaningful: imported Letterboxd titles are stored
        in watchlist order, and manual additions are inserted at the top.
        """
        with self._lock:
            try:
                data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                data = []
            return data

    def save(self, movies: list[dict[str, Any]]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(movies, indent=2, ensure_ascii=False))
            os.replace(tmp, self.path)

    def _watch_history(self, movie: dict[str, Any]) -> list[dict[str, Any]]:
        raw_history = movie.get("watch_history")
        history: list[dict[str, Any]] = []
        if isinstance(raw_history, list):
            for index, raw_event in enumerate(raw_history):
                if isinstance(raw_event, dict):
                    original: dict[str, Any] = dict(raw_event)
                    raw_date = raw_event.get("watched_on") or raw_event.get("watched_at") or ""
                    recorded_at = str(raw_event.get("recorded_at") or raw_event.get("created_at") or "")
                    raw_id = str(raw_event.get("id") or "")
                elif isinstance(raw_event, str):
                    original = {}
                    raw_date = raw_event
                    recorded_at = ""
                    raw_id = ""
                else:
                    continue
                try:
                    watched_on = normalize_watch_date(raw_date)
                except ValueError:
                    watched_on = ""
                event_id = raw_id or _watch_event_id(
                    str(movie.get("id") or "movie"), watched_on, f"{recorded_at or raw_date}:{index}"
                )
                history.append({**original, "id": event_id, "watched_on": watched_on, "recorded_at": recorded_at})

        if not history and movie.get("watched"):
            legacy_at = str(movie.get("watched_at") or "")
            try:
                watched_on = normalize_watch_date(legacy_at)
            except ValueError:
                watched_on = ""
            history.append({
                "id": _watch_event_id(str(movie.get("id") or "movie"), watched_on, legacy_at or "legacy"),
                "watched_on": watched_on,
                "recorded_at": legacy_at,
            })
        return history

    @staticmethod
    def _latest_event(history: list[dict[str, str]]) -> dict[str, str] | None:
        if not history:
            return None
        return max(history, key=lambda event: (
            event.get("watched_on") or "0000-00-00",
            event.get("recorded_at") or "",
            event.get("id") or "",
        ))

    def project(self, movie: dict[str, Any]) -> dict[str, Any]:
        """Expose queue membership and watch history without rewriting legacy data."""
        projected = dict(movie)
        history = self._watch_history(movie)
        latest = self._latest_event(history)
        projected["watch_history"] = sorted(
            history,
            key=lambda event: (event.get("watched_on") or "0000-00-00", event.get("recorded_at") or ""),
            reverse=True,
        )
        projected["watched"] = bool(history) or bool(movie.get("watched"))
        projected["queued"] = bool(movie.get("queued")) if "queued" in movie else not bool(movie.get("watched"))
        projected["latest_watched_on"] = latest.get("watched_on", "") if latest else ""
        projected["watch_count"] = len(history)
        return projected

    def all_projected(self) -> list[dict[str, Any]]:
        return [self.project(movie) for movie in self.all()]

    def get_projected(self, movie_id: str) -> dict[str, Any] | None:
        movie = self.get(movie_id)
        return self.project(movie) if movie else None

    def _persist_history(self, movie: dict[str, Any], history: list[dict[str, str]]) -> None:
        latest = self._latest_event(history)
        movie["watch_history"] = history
        movie["watched"] = bool(history)
        movie["watched_at"] = latest.get("watched_on", "") if latest else ""

    def get(self, movie_id: str) -> dict[str, Any] | None:
        for movie in self.all():
            if movie.get("id") == movie_id:
                return movie
        return None

    def add_many(self, movies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.add_or_bump(movies)["added"]

    def add_or_bump(self, movies: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Add new titles to the top of the queue.

        A title already in the list is never duplicated; its missing card fields are filled and (movies#23) it
        moves to the top: a watched title is queued again with its
        history kept, an archived one is restored first. Each bumped entry reports what it `was`.
        """
        with self._lock:
            existing = self.all()
            by_id = {m["id"]: m for m in existing if "id" in m}
            by_title = {slugify(m.get("title", ""), None): m for m in existing if m.get("title")}
            added: list[dict[str, Any]] = []
            bumped: list[dict[str, Any]] = []
            touched: set[str] = set()
            now = datetime.now(timezone.utc).isoformat()
            for movie in movies:
                title = str(movie.get("title", "")).strip()
                if not title:
                    continue
                year = movie.get("year")
                year = int(year) if str(year).isdigit() else None
                movie_id = movie.get("id") or slugify(title, year)
                title_key = slugify(title, None)
                duplicate = by_id.get(movie_id) or (by_title.get(title_key) if year is None else None)
                if duplicate:
                    current = duplicate
                    for key in ("poster", "letterboxd_url", "source"):
                        if movie.get(key) and not current.get(key):
                            current[key] = movie[key]
                    if current.get("id") not in touched:
                        before = self.project(current)
                        was = "archived" if before.get("archived") else ("queued" if before.get("queued") else "watched")
                        if before.get("archived"):
                            current["archived"] = False
                            current.pop("archived_at", None)
                        if not before.get("queued"):
                            self._persist_history(current, self._watch_history(current))
                            current["queued"] = True
                        existing.insert(0, existing.pop(next(i for i, m in enumerate(existing) if m is current)))
                        touched.add(current.get("id"))
                        bumped.append({**self.project(current), "was": was})
                    continue
                record = {
                    "id": movie_id,
                    "title": title,
                    "year": year,
                    "poster": movie.get("poster") or "",
                    "letterboxd_url": movie.get("letterboxd_url") or "",
                    "source": movie.get("source") or "manual",
                    "notes": movie.get("notes") or "",
                    "watched": bool(movie.get("watched", False)),
                    "queued": not bool(movie.get("watched", False)),
                    "watch_history": [],
                    "added_at": movie.get("added_at") or now,
                }
                existing.insert(0, record)
                by_id[movie_id] = record
                touched.add(movie_id)
                added.append(record)
            self.save(existing)
            return {"added": added, "bumped": bumped}

    def mark_watched(self, movie_id: str, watched_on: str | None = None) -> dict[str, Any]:
        watch_date = normalize_watch_date(watched_on or datetime.now(timezone.utc).date().isoformat())
        with self._lock:
            movies = self.all()
            for movie in movies:
                if movie.get("id") == movie_id:
                    history = self._watch_history(movie)
                    recorded_at = datetime.now(timezone.utc).isoformat()
                    history.append({
                        "id": _watch_event_id(movie_id, watch_date, recorded_at),
                        "watched_on": watch_date,
                        "recorded_at": recorded_at,
                    })
                    self._persist_history(movie, history)
                    movie["queued"] = False
                    self.save(movies)
                    return self.project(movie)
        raise KeyError(movie_id)

    def queue_again(self, movie_id: str) -> dict[str, Any]:
        with self._lock:
            movies = self.all()
            for index, movie in enumerate(movies):
                if movie.get("id") == movie_id:
                    history = self._watch_history(movie)
                    self._persist_history(movie, history)
                    movie["queued"] = True
                    moved = movies.pop(index)
                    movies.insert(0, moved)
                    self.save(movies)
                    return self.project(moved)
        raise KeyError(movie_id)

    def edit_watch_date(self, movie_id: str, event_id: str, watched_on: str) -> dict[str, Any]:
        watch_date = normalize_watch_date(watched_on)
        if not watch_date:
            raise ValueError("watched_on is required")
        with self._lock:
            movies = self.all()
            for movie in movies:
                if movie.get("id") != movie_id:
                    continue
                history = self._watch_history(movie)
                for event in history:
                    if event.get("id") == event_id:
                        event["watched_on"] = watch_date
                        self._persist_history(movie, history)
                        self.save(movies)
                        return self.project(movie)
                raise KeyError(event_id)
        raise KeyError(movie_id)

    def delete_watch_event(self, movie_id: str, event_id: str, *, requeue: bool = False) -> dict[str, Any]:
        """Remove one watch event and keep the film reachable when history becomes empty."""
        with self._lock:
            movies = self.all()
            for movie in movies:
                if movie.get("id") != movie_id:
                    continue
                history = self._watch_history(movie)
                remaining = [event for event in history if event.get("id") != event_id]
                if len(remaining) == len(history):
                    raise KeyError(event_id)
                self._persist_history(movie, remaining)
                if requeue or not remaining:
                    movie["queued"] = True
                self.save(movies)
                return self.project(movie)
        raise KeyError(movie_id)

    def toggle_watched(self, movie_id: str) -> dict[str, Any]:
        with self._lock:
            movie = self.get_projected(movie_id)
            if not movie:
                raise KeyError(movie_id)
            if not movie.get("queued"):
                raise WatchStateConflict("Refresh Movies to manage watch history")
            return self.mark_watched(movie_id)

    def move_to_top(self, movie_id: str) -> dict[str, Any]:
        with self._lock:
            movies = self.all()
            for index, movie in enumerate(movies):
                if movie.get("id") == movie_id:
                    moved = movies.pop(index)
                    movies.insert(0, moved)
                    self.save(movies)
                    return self.project(moved)
        raise KeyError(movie_id)

    def set_archived(self, movie_id: str, archived: bool) -> dict[str, Any]:
        """Set the soft-archive flag in place, preserving record index."""
        with self._lock:
            movies = self.all()
            for movie in movies:
                if movie.get("id") == movie_id:
                    if archived:
                        movie["archived"] = True
                        movie["archived_at"] = datetime.now(timezone.utc).isoformat()
                    else:
                        movie["archived"] = False
                        movie.pop("archived_at", None)
                    self.save(movies)
                    return self.project(movie)
        raise KeyError(movie_id)

    def update_metadata(self, movie_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Merge validated card metadata in place; queue order and watch history are untouched."""
        protected = {"id", "watch_history", "watched", "watched_at", "queued", "archived", "archived_at", "added_at"}
        with self._lock:
            movies = self.all()
            for movie in movies:
                if movie.get("id") == movie_id:
                    for key, value in fields.items():
                        if key not in protected:
                            movie[key] = value
                    movie["updated_at"] = datetime.now(timezone.utc).isoformat()
                    self.save(movies)
                    return self.project(movie)
        raise KeyError(movie_id)

    def delete(self, movie_id: str) -> None:
        with self._lock:
            movies = [m for m in self.all() if m.get("id") != movie_id]
            self.save(movies)
