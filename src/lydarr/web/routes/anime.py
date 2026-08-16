"""Anime and manga watchlist management routes."""
import asyncio
import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from anilist.search import airing_schedules, search as anilist_search, find_by_title
from anilist.types import AnilistError, MediaType
from lydarr.file_manager import MediaEntry
from lydarr.tracker import DEFAULT_CHECK_DELAY, _safe_dirname
from lydarr.web.routes.daemon import _daemon_running, spawn_tracker

_logger = logging.getLogger("lydarr.web")

router = APIRouter(prefix = "/api")

# Air times and status change rarely, so cache lookups to avoid hammering the
# AniList API when several clients load the watchlist at once (each entry would
# otherwise trigger a fresh request on every page load, per client).
_STATUS_TTL = 600.0
# Calendar ranges are re-requested every time the user pages through months.
_SCHEDULE_TTL = 300.0


@router.get("/anime/search")
async def search(q: str, type: str = "anime"):
    media_type = MediaType.MANGA if type == "manga" else MediaType.ANIME
    results = await anilist_search(q, media_type)
    return [
        {
            "id": m.id,
            "title": m.display_title(),
            "type": m.media_type.value.lower(),
            "status": m.status.value,
            "episodes": m.episodes,
            "chapters": m.chapters,
            "next_airing_at": m.next_airing_at,
            "next_airing_episode": m.next_airing_episode,
            "cover_image": m.cover_image,
        }
        for m in results
    ]


@router.get("/anime/list")
async def list_media(request: Request):
    default_dir = request.app.state.cfg.default_dir
    return [
        {
            "title": e.title,
            "type": e.media_type,
            "submitters": e.submitters,
            "last_chapter": e.last_chapter,
            "search_name": e.search_name,
            "deprecated": e.deprecated,
            "download_dir": e.download_dir,
            "default_path": os.path.join(default_dir, _safe_dirname(e.title)),
            "check_delay": e.check_delay,
            "default_check_delay": DEFAULT_CHECK_DELAY,
        }
        for e in request.app.state.anime_state.entries()
    ]


class AddBody(BaseModel):
    title: str
    type: Literal["anime", "manga"] = "anime"
    submitters: list[str] = []


class TitleBody(BaseModel):
    title: str


class SubmittersBody(BaseModel):
    title: str
    submitters: list[str]
    search_name: str = ""
    download_dir: str = ""
    check_delay: int = 0


@router.post("/anime/add")
async def add_media(body: AddBody, request: Request):
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if body.title in state.titles():
        return {"added": False, "reason": "already tracked"}
    entry = MediaEntry(
        title = body.title,
        media_type = body.type,
        submitters = body.submitters,
    )
    await state.add(cfg.anime_file, entry)
    if _daemon_running(request.app):
        spawn_tracker(request.app, entry)
        _logger.info("[%s] Added to watchlist; now tracking.", body.title)
    else:
        _logger.info("[%s] Added to watchlist; will track when the daemon starts.", body.title)
    media_type = MediaType.MANGA if body.type == "manga" else MediaType.ANIME
    try:
        status = await _cached_status(request.app, body.title, media_type)
    except Exception:
        status = None
    return {"added": True, "status": status}


@router.post("/anime/remove")
async def remove_media(body: TitleBody, request: Request):
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if body.title not in state.titles():
        return {"removed": False, "reason": "not tracked"}
    await state.remove(cfg.anime_file, body.title)
    return {"removed": True}


@router.post("/anime/deprecate")
async def deprecate_media(body: TitleBody, request: Request):
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.set_deprecated(cfg.anime_file, body.title, True)
    return {"ok": True}


@router.post("/anime/reactivate")
async def reactivate_media(body: TitleBody, request: Request):
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.set_deprecated(cfg.anime_file, body.title, False)
    return {"ok": True}


@router.post("/anime/submitters")
async def update_submitters(body: SubmittersBody, request: Request):
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.update_entry(cfg.anime_file, body.title, body.submitters, body.search_name,
                             body.download_dir, max(0, body.check_delay))
    return {"ok": True}


async def _resolve_status(title: str, media_type: MediaType) -> dict:
    info = await find_by_title(title, media_type)
    if info is None:
        return {"id": None, "status": None, "next_airing_at": None, "next_airing_episode": None}
    return {
        "id": info.id,
        "status": info.status.value,
        "next_airing_at": info.next_airing_at,
        "next_airing_episode": info.next_airing_episode,
    }


async def _cached(app, cache: dict, inflight: dict, key, factory, ttl: float):
    async with app.state.status_lock:
        hit = cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        # Single-flight: concurrent requests for the same key share one lookup.
        task = inflight.get(key)
        owner = task is None
        if owner:
            task = asyncio.create_task(factory())
            inflight[key] = task

    if not owner:
        return await task

    try:
        payload = await task
    except BaseException:
        async with app.state.status_lock:
            inflight.pop(key, None)
        raise
    async with app.state.status_lock:
        inflight.pop(key, None)
        cache[key] = (time.monotonic() + ttl, payload)
    return payload


async def _cached_status(app, title: str, media_type: MediaType) -> dict:
    return await _cached(
        app, app.state.status_cache, app.state.status_inflight,
        (title, media_type.value),
        lambda: _resolve_status(title, media_type),
        _STATUS_TTL,
    )


async def _resolve_schedule(media_ids: tuple[int, ...], start: int, end: int) -> list[dict]:
    episodes = await airing_schedules(list(media_ids), start, end)
    return [
        {
            "media_id": e.media_id,
            "episode": e.episode,
            "airing_at": e.airing_at,
            "duration": e.duration,
        }
        for e in episodes
    ]


@router.get("/anime/status")
async def get_status(title: str, request: Request, type: str = "anime"):
    media_type = MediaType.MANGA if type == "manga" else MediaType.ANIME
    return await _cached_status(request.app, title, media_type)


@router.get("/anime/calendar")
async def get_calendar(start: int, end: int, request: Request):
    """Episodes airing between two unix timestamps, for every tracked anime."""
    if end <= start:
        return {"episodes": [], "unresolved": []}

    entries = [
        e for e in request.app.state.anime_state.entries()
        if e.media_type != "manga" and not e.deprecated
    ]
    lookups = await asyncio.gather(
        *(_cached_status(request.app, e.title, MediaType.ANIME) for e in entries),
        return_exceptions = True,
    )

    titles: dict[int, str] = {}
    unresolved: list[str] = []
    for entry, payload in zip(entries, lookups):
        media_id = payload.get("id") if isinstance(payload, dict) else None
        if media_id is None:
            unresolved.append(entry.title)
        else:
            titles[media_id] = entry.title

    if not titles:
        return {"episodes": [], "unresolved": unresolved}

    media_ids = tuple(sorted(titles))
    try:
        episodes = await _cached(
            request.app, request.app.state.schedule_cache, request.app.state.schedule_inflight,
            (media_ids, start, end),
            lambda: _resolve_schedule(media_ids, start, end),
            _SCHEDULE_TTL,
        )
    except (AnilistError, OSError) as exc:
        _logger.warning("Calendar lookup failed: %s", exc)
        return {"episodes": [], "unresolved": unresolved, "error": str(exc)}

    return {
        "episodes": [
            {**e, "title": titles[e["media_id"]]}
            for e in episodes if e["media_id"] in titles
        ],
        "unresolved": unresolved,
    }
