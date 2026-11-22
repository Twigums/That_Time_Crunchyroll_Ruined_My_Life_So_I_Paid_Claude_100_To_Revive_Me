"""Persistent tracker state: what was grabbed, what is pending, what needs a look.

The watchlist (anime.toml) says what to track; this file remembers what happened,
so a restart neither re-downloads nor forgets an episode.
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from functools import partial

_logger = logging.getLogger("lydarr.state")

PENDING = "pending"
GRABBED = "grabbed"
REVIEW = "review"
MISSED = "missed"
SKIPPED = "skipped"
SETTLED = (GRABBED, MISSED, SKIPPED)

ACTIVITY_LIMIT = 500
_STATE_VERSION = 1


@dataclass
class EpisodeRecord:
    status: str = PENDING
    first_check: float = 0.0
    next_check: float = 0.0
    checks: int = 0
    note: str = ""
    hash: str = ""
    name: str = ""
    at: float = 0.0
    candidates: list[dict] = field(default_factory = list)


@dataclass
class ShowState:
    since: float = 0.0
    backfill: bool = False
    offsets: dict[str, int] = field(default_factory = dict)
    extra_offsets: list[int] = field(default_factory = list)
    completed_at: float = 0.0
    episodes: dict[int, EpisodeRecord] = field(default_factory = dict)


def _show_from_dict(raw: dict) -> ShowState:
    episodes = {
        int(number): EpisodeRecord(**{k: v for k, v in rec.items() if k in EpisodeRecord.__dataclass_fields__})
        for number, rec in (raw.get("episodes") or {}).items()
    }
    return ShowState(
        since = float(raw.get("since", 0.0)),
        backfill = bool(raw.get("backfill", False)),
        offsets = {str(k): int(v) for k, v in (raw.get("offsets") or {}).items()},
        extra_offsets = [int(v) for v in raw.get("extra_offsets") or []],
        completed_at = float(raw.get("completed_at", 0.0)),
        episodes = episodes,
    )


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok = True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding = "utf-8") as f:
        json.dump(payload, f, ensure_ascii = False, indent = 1)
    os.replace(tmp, path)


class TrackerState:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock: asyncio.Lock | None = None
        self.enabled = False
        self._shows: dict[str, ShowState] = {}
        self._reap: list[str] = []
        self._activity: list[dict] = []
        self._next_id = 1

    # ── persistence ───────────────────────────────────────────────────────

    def load(self) -> None:
        try:
            with open(self._path, encoding = "utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            _logger.error("Could not read tracker state %s (%s); starting empty.", self._path, exc)
            return
        try:
            self.enabled = bool(raw.get("tracker_enabled", False))
            self._shows = {title: _show_from_dict(show) for title, show in (raw.get("shows") or {}).items()}
            self._reap = [str(h) for h in raw.get("reap") or []]
            self._activity = list(raw.get("activity") or [])
            self._next_id = 1 + max((int(a.get("id", 0)) for a in self._activity), default = 0)
        except (TypeError, ValueError) as exc:
            _logger.error("Tracker state %s is malformed (%s); starting empty.", self._path, exc)
            self.__init__(self._path)

    def _payload(self) -> dict:
        return {
            "version": _STATE_VERSION,
            "tracker_enabled": self.enabled,
            "shows": {
                title: {**asdict(show), "episodes": {str(n): asdict(rec) for n, rec in sorted(show.episodes.items())}}
                for title, show in self._shows.items()
            },
            "reap": self._reap,
            "activity": self._activity,
        }

    async def save(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, partial(_write_json, self._path, self._payload()),
                )
            except OSError as exc:
                _logger.error("Could not write tracker state %s: %s", self._path, exc)

    # ── shows and episodes ────────────────────────────────────────────────

    def peek(self, title: str) -> ShowState | None:
        return self._shows.get(title)

    def show(self, title: str, now: float | None = None) -> ShowState:
        """The show's state, created on first sight with tracking starting now."""
        found = self._shows.get(title)
        if found is None:
            found = ShowState(since = time.time() if now is None else now)
            self._shows[title] = found
        return found

    def episode(self, title: str, number: int) -> EpisodeRecord:
        show = self.show(title)
        rec = show.episodes.get(number)
        if rec is None:
            rec = EpisodeRecord()
            show.episodes[number] = rec
        return rec

    def forget(self, title: str) -> None:
        self._shows.pop(title, None)

    def reviews(self) -> list[tuple[str, int, EpisodeRecord]]:
        return [
            (title, number, rec)
            for title, show in self._shows.items()
            for number, rec in sorted(show.episodes.items())
            if rec.status == REVIEW
        ]

    # ── torrents to drop from Transmission once complete ──────────────────

    def reap_hashes(self) -> list[str]:
        return list(self._reap)

    def watch(self, info_hash: str) -> None:
        if info_hash and info_hash not in self._reap:
            self._reap.append(info_hash)

    def unwatch(self, hashes: list[str]) -> None:
        gone = set(hashes)
        self._reap = [h for h in self._reap if h not in gone]

    # ── activity log ──────────────────────────────────────────────────────

    def log(self, kind: str, title: str, episode: int | None = None, *, name: str = "",
            info_hash: str = "", directory: str = "", detail: str = "", now: float | None = None) -> dict:
        event = {
            "id": self._next_id,
            "ts": time.time() if now is None else now,
            "kind": kind,
            "title": title,
            "episode": episode,
            "name": name,
            "hash": info_hash,
            "dir": directory,
            "detail": detail,
        }
        self._next_id += 1
        self._activity.append(event)
        if len(self._activity) > ACTIVITY_LIMIT:
            self._activity = self._activity[-ACTIVITY_LIMIT:]
        return event

    def event(self, event_id: int) -> dict | None:
        return next((e for e in self._activity if e.get("id") == event_id), None)

    def activity(self, limit: int = 200) -> list[dict]:
        return list(reversed(self._activity[-limit:]))
