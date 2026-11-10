"""Disk-backed cache of everything lydarr knows from AniList.

The tracker, the watchlist and the calendar all read from here. AniList itself
is only contacted by a refresh: a few batched requests every few hours, never
one per title. When AniList is slow, rate limiting or down, the last good data
keeps being served and the next attempt backs off.
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, fields
from functools import partial

from anilist.search import airing_schedules, find_by_title, media_by_ids, prequel_episode_counts
from anilist.types import AiringEpisode, AnilistError, AnilistMedia, MediaStatus, MediaType
from lydarr.file_manager import MediaEntry, MediaState

_logger = logging.getLogger("lydarr.anilist")

REFRESH_INTERVAL = 6 * 3600
RESOLVE_RETRY = 3600
SCHEDULE_PAST = 21 * 86400
SCHEDULE_AHEAD = 45 * 86400
FORCE_MIN_GAP = 30
PREQUEL_RETRY = 600
_FAILURE_BACKOFF = (300, 900, 1800, 3600, 7200)
_CACHE_VERSION = 1
_MEDIA_FIELDS = {f.name for f in fields(AnilistMedia)}


def _is_active_anime(entry: MediaEntry) -> bool:
    return entry.media_type != "manga" and not entry.deprecated


def _media_to_dict(m: AnilistMedia) -> dict:
    d = asdict(m)
    d["media_type"] = m.media_type.value
    d["status"] = m.status.value
    return d


def _media_from_dict(d: dict) -> AnilistMedia:
    known = {k: v for k, v in d.items() if k in _MEDIA_FIELDS}
    known["media_type"] = MediaType(known["media_type"])
    try:
        known["status"] = MediaStatus(known["status"])
    except ValueError:
        known["status"] = MediaStatus.UNKNOWN
    return AnilistMedia(**known)


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok = True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding = "utf-8") as f:
        json.dump(payload, f, ensure_ascii = False)
    os.replace(tmp, path)


class MediaStore:
    def __init__(self, cache_file: str, media_state: MediaState, anime_file: str) -> None:
        self._path = cache_file
        self._state = media_state
        self._anime_file = anime_file
        self._media: dict[int, AnilistMedia] = {}
        self._schedule: dict[int, dict[int, int]] = {}
        self._prequels: dict[int, list[int]] = {}
        self._scheduled_ids: set[int] = set()
        self._missing_ids: set[int] = set()
        self._full_schedule: set[int] = set()
        self._window: tuple[int, int] | None = None
        self._unresolved: dict[str, float] = {}
        self._refreshed_at = 0.0
        self._error = ""
        self._failures = 0
        self._retry_at = 0.0
        self._prequel_retry_at = 0.0
        self._forced_at = 0.0
        self._task: asyncio.Task | None = None
        self._window_lock: asyncio.Lock | None = None

    # ── persistence ───────────────────────────────────────────────────────

    def load(self) -> None:
        try:
            with open(self._path, encoding = "utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            _logger.warning("Ignoring unreadable AniList cache %s: %s", self._path, exc)
            return
        if raw.get("version") != _CACHE_VERSION:
            return
        try:
            self._media = {int(k): _media_from_dict(v) for k, v in raw.get("media", {}).items()}
            self._schedule = {
                int(k): {int(ep): int(at) for ep, at in rows.items()}
                for k, rows in raw.get("schedule", {}).items()
            }
            self._prequels = {int(k): [int(n) for n in v] for k, v in raw.get("prequels", {}).items()}
            self._scheduled_ids = {int(i) for i in raw.get("scheduled_ids", [])}
            self._full_schedule = {int(i) for i in raw.get("full_schedule", [])}
            window = raw.get("window")
            self._window = (int(window[0]), int(window[1])) if window else None
            self._unresolved = {str(k): float(v) for k, v in raw.get("unresolved", {}).items()}
            self._refreshed_at = float(raw.get("refreshed_at", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            _logger.warning("Ignoring malformed AniList cache %s: %s", self._path, exc)
            self.__init__(self._path, self._state, self._anime_file)

    def _payload(self) -> dict:
        return {
            "version": _CACHE_VERSION,
            "refreshed_at": self._refreshed_at,
            "window": list(self._window) if self._window else None,
            "scheduled_ids": sorted(self._scheduled_ids),
            "full_schedule": sorted(self._full_schedule),
            "unresolved": self._unresolved,
            "media": {str(k): _media_to_dict(m) for k, m in self._media.items()},
            "schedule": {str(k): {str(ep): at for ep, at in rows.items()} for k, rows in self._schedule.items()},
            "prequels": {str(k): v for k, v in self._prequels.items()},
        }

    async def _save(self) -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, partial(_write_json, self._path, self._payload()))
        except OSError as exc:
            _logger.warning("Could not write AniList cache %s: %s", self._path, exc)

    # ── lookups (never touch the network) ─────────────────────────────────

    def info(self, entry: MediaEntry) -> AnilistMedia | None:
        return self._media.get(entry.anilist_id) if entry.anilist_id else None

    def episodes(self, entry: MediaEntry) -> list[tuple[int, int]]:
        """(episode, airing_at) for every scheduled episode we know of, in order."""
        return sorted(self._schedule.get(entry.anilist_id, {}).items())

    def airing_at(self, entry: MediaEntry, episode: int) -> int | None:
        return self._schedule.get(entry.anilist_id, {}).get(episode)

    def next_episode(self, entry: MediaEntry, now: float) -> tuple[int, int] | None:
        upcoming = [(at, ep) for ep, at in self._schedule.get(entry.anilist_id, {}).items() if at > now]
        if upcoming:
            at, ep = min(upcoming)
            return ep, at
        info = self.info(entry)
        if info and info.next_airing_episode and info.next_airing_at and info.next_airing_at > now:
            return info.next_airing_episode, info.next_airing_at
        return None

    def prequels(self, entry: MediaEntry) -> list[int] | None:
        """Earlier seasons' episode counts, nearest first; None until fetched."""
        return self._prequels.get(entry.anilist_id)

    def has_full_schedule(self, entry: MediaEntry) -> bool:
        return entry.anilist_id in self._full_schedule

    def describe(self, entry: MediaEntry, now: float) -> dict:
        info = self.info(entry)
        if info is None:
            return {
                "id": entry.anilist_id or None, "status": None, "display_title": None,
                "next_airing_at": None, "next_airing_episode": None,
                "episodes": None, "cover_image": None, "format": None,
                "unresolved": entry.title in self._unresolved,
            }
        upcoming = self.next_episode(entry, now) if entry.media_type != "manga" else None
        return {
            "id": info.id,
            "status": info.status.value,
            "display_title": info.display_title(),
            "next_airing_episode": upcoming[0] if upcoming else None,
            "next_airing_at": upcoming[1] if upcoming else None,
            "episodes": info.episodes,
            "cover_image": info.cover_image,
            "format": info.format,
            "unresolved": False,
        }

    def calendar(self, start: int, end: int) -> list[dict]:
        out: list[dict] = []
        for entry in self._state.entries():
            if not _is_active_anime(entry) or not entry.anilist_id:
                continue
            info = self._media.get(entry.anilist_id)
            for episode, at in self._schedule.get(entry.anilist_id, {}).items():
                if start <= at <= end:
                    out.append({
                        "media_id": entry.anilist_id,
                        "episode": episode,
                        "airing_at": at,
                        "duration": info.duration if info else None,
                        "title": entry.title,
                    })
        out.sort(key = lambda e: (e["airing_at"], e["title"]))
        return out

    def unresolved_titles(self) -> list[str]:
        return [e.title for e in self._state.entries() if not e.anilist_id and e.title in self._unresolved]

    def status(self) -> dict:
        now = time.time()
        return {
            "as_of": self._refreshed_at or None,
            "refreshing": self.refreshing(),
            "error": self._error or None,
            "retry_in": max(0, round(self._retry_at - now)) if self._error else None,
            "unresolved": self.unresolved_titles(),
        }

    # ── refreshing ────────────────────────────────────────────────────────

    def refreshing(self) -> bool:
        return self._task is not None and not self._task.done()

    def needs_refresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if now < self._retry_at:
            return False
        entries = self._state.entries()
        for e in entries:
            if not e.anilist_id:
                if now - self._unresolved.get(e.title, 0.0) >= RESOLVE_RETRY:
                    return True
            elif e.anilist_id in self._missing_ids:
                continue
            elif e.anilist_id not in self._media:
                return True
            elif _is_active_anime(e) and e.anilist_id not in self._scheduled_ids:
                return True
        return bool(entries) and now - self._refreshed_at >= REFRESH_INTERVAL

    def _missing_prequels(self) -> list[int]:
        return sorted({
            e.anilist_id for e in self._state.entries()
            if _is_active_anime(e) and e.anilist_id and e.anilist_id not in self._prequels
        })

    def refresh_soon(self, force: bool = False) -> asyncio.Task | None:
        """Start a background refresh if one is due; returns the task, if any."""
        if self.refreshing():
            return self._task
        now = time.time()
        if force:
            if now - self._forced_at < FORCE_MIN_GAP:
                return None
            self._forced_at = now
        elif not self.needs_refresh(now):
            if self._missing_prequels() and now >= max(self._retry_at, self._prequel_retry_at):
                self._task = asyncio.create_task(self._fill_prequels())
                return self._task
            return None
        self._task = asyncio.create_task(self._refresh(force))
        return self._task

    async def refresh(self, force: bool = False) -> bool:
        """Refresh if due (or forced) and wait for it; True when the cache is healthy."""
        task = self.refresh_soon(force)
        if task is not None:
            await asyncio.shield(task)
        return not self._error

    def _note_failure(self, exc: Exception) -> None:
        self._failures += 1
        message = str(exc) or type(exc).__name__
        retry_after = getattr(exc, "retry_after", None)
        delay = retry_after if retry_after else _FAILURE_BACKOFF[min(self._failures, len(_FAILURE_BACKOFF)) - 1]
        self._error = message
        self._retry_at = time.time() + delay
        age = f"{round((time.time() - self._refreshed_at) / 60)} min old" if self._refreshed_at else "empty"
        _logger.warning(
            "AniList refresh failed: %s. Serving cached data (%s); next attempt in %d min.",
            message, age, max(1, round(delay / 60)),
        )

    async def _resolve_ids(self, now: float, force: bool) -> None:
        for e in self._state.entries():
            if e.anilist_id:
                continue
            if not force and now - self._unresolved.get(e.title, 0.0) < RESOLVE_RETRY:
                continue
            media_type = MediaType.MANGA if e.media_type == "manga" else MediaType.ANIME
            found = await find_by_title(e.title, media_type)
            if found is None:
                self._unresolved[e.title] = now
                _logger.info("[%s] Not found on AniList.", e.title)
                continue
            self._unresolved.pop(e.title, None)
            self._media[found.id] = found
            await self._state.set_anilist_id(self._anime_file, e.title, found.id)
            _logger.info("[%s] Matched to AniList #%d (%s).", e.title, found.id, found.display_title())

    def _merge_schedule(self, ids: list[int], rows: list[AiringEpisode], start: int, end: int) -> None:
        for media_id in ids:
            current = self._schedule.get(media_id, {})
            self._schedule[media_id] = {ep: at for ep, at in current.items() if not start <= at <= end}
        for row in rows:
            self._schedule.setdefault(row.media_id, {})[row.episode] = row.airing_at

    def _prune(self, keep: set[int]) -> None:
        for table in (self._media, self._schedule, self._prequels):
            for key in [k for k in table if k not in keep]:
                del table[key]
        self._full_schedule &= keep
        titles = set(self._state.titles())
        self._unresolved = {t: at for t, at in self._unresolved.items() if t in titles}

    async def _refresh(self, force: bool) -> None:
        now = time.time()
        try:
            await self._resolve_ids(now, force)
            entries = self._state.entries()
            ids = sorted({e.anilist_id for e in entries if e.anilist_id})
            if ids:
                found = await media_by_ids(ids)
                for m in found:
                    self._media[m.id] = m
                self._missing_ids = set(ids) - {m.id for m in found}
            anime_ids = sorted({e.anilist_id for e in entries if _is_active_anime(e) and e.anilist_id})
            start, end = int(now) - SCHEDULE_PAST, int(now) + SCHEDULE_AHEAD
            if anime_ids:
                rows = await airing_schedules(anime_ids, start, end)
                self._merge_schedule(anime_ids, rows, start, end)
            self._scheduled_ids = set(anime_ids)
            self._window = (start, end)
            self._prune(set(ids))
            self._refreshed_at = time.time()
            self._error = ""
            self._failures = 0
            self._retry_at = 0.0
        except AnilistError as exc:
            self._note_failure(exc)
        except Exception as exc:
            _logger.exception("Unexpected error while refreshing AniList data")
            self._note_failure(exc)
        await self._save()
        if not self._error:
            await self._fill_prequels()

    async def _fill_prequels(self) -> None:
        for media_id in self._missing_prequels():
            try:
                self._prequels[media_id] = await prequel_episode_counts(media_id)
            except AnilistError as exc:
                self._prequel_retry_at = time.time() + PREQUEL_RETRY
                _logger.info("Could not fetch earlier seasons for AniList #%d yet: %s", media_id, exc)
                break
            await self._save()

    # ── targeted fetches ──────────────────────────────────────────────────

    def seed(self, media: AnilistMedia) -> None:
        """Remember media we already fetched (e.g. a search result being added)."""
        self._media[media.id] = media

    async def ensure_window(self, start: int, end: int) -> None:
        """Make sure the cached schedule covers [start, end]; raises AnilistError if it cannot."""
        if self._window_lock is None:
            self._window_lock = asyncio.Lock()
        async with self._window_lock:
            if self._window is None:
                await self.refresh()
            if self._window is None:
                raise AnilistError(self._error or "AniList data is not available yet")
            lo, hi = self._window
            ids = sorted(self._scheduled_ids)
            if not ids or (start >= lo and end <= hi):
                return
            if start < lo:
                self._merge_schedule(ids, await airing_schedules(ids, start, lo), start, lo)
                lo = start
            if end > hi:
                self._merge_schedule(ids, await airing_schedules(ids, hi, end), hi, end)
                hi = end
            self._window = (lo, hi)
            await self._save()

    async def fetch_full_schedule(self, entry: MediaEntry) -> None:
        """Fetch every episode's air time for one show (used when backfilling)."""
        if not entry.anilist_id:
            raise AnilistError(f"{entry.title} is not matched to AniList yet")
        rows = await airing_schedules([entry.anilist_id])
        table = self._schedule.setdefault(entry.anilist_id, {})
        for row in rows:
            table[row.episode] = row.airing_at
        self._full_schedule.add(entry.anilist_id)
        await self._save()
