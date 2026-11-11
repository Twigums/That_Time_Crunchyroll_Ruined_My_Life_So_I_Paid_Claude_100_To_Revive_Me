"""Watchlist, AniList search and calendar routes.

Nothing here calls AniList per entry: statuses and the calendar come from the
shared media store, which refreshes itself in the background.
"""
import logging
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from anilist.search import search as anilist_search
from anilist.types import AnilistError, MediaType
from lydarr.file_manager import MediaEntry
from lydarr.paths import default_download_dir, resolve_download_dir
from lydarr.tracker import DEFAULT_CHECK_DELAY

_logger = logging.getLogger("lydarr.web")

router = APIRouter(prefix = "/api")

_SEARCH_TTL = 600.0
_SEARCH_CACHE_MAX = 100


def entry_payload(app, entry: MediaEntry, now: float) -> dict:
    cfg = app.state.cfg
    info = app.state.store.info(entry)
    return {
        "title": entry.title,
        "type": entry.media_type,
        "anilist_id": entry.anilist_id or None,
        "submitters": entry.submitters,
        "last_chapter": entry.last_chapter,
        "search_name": entry.search_name,
        "deprecated": entry.deprecated,
        "download_dir": entry.download_dir,
        "default_path": default_download_dir(cfg, entry, info),
        "path": resolve_download_dir(cfg, entry, info),
        "check_delay": entry.check_delay,
        "default_check_delay": DEFAULT_CHECK_DELAY,
        "episode_offset": entry.episode_offset,
        "status": app.state.store.describe(entry, now),
        "progress": app.state.tracker.progress(entry),
    }


@router.get("/anime/list")
async def list_media(request: Request):
    app = request.app
    app.state.store.refresh_soon()
    now = time.time()
    return {
        "entries": [entry_payload(app, e, now) for e in app.state.anime_state.entries()],
        "anilist": app.state.store.status(),
        "default_dir": app.state.cfg.default_dir,
    }


@router.post("/anime/refresh")
async def refresh(request: Request):
    """Ask for fresh AniList data now; the list endpoint reports when it lands."""
    started = request.app.state.store.refresh_soon(force = True) is not None
    return {"ok": True, "started": started, "anilist": request.app.state.store.status()}


@router.get("/anime/search")
async def search(q: str, request: Request, type: str = "anime"):
    app = request.app
    media_type = MediaType.MANGA if type == "manga" else MediaType.ANIME
    key = (q.strip().lower(), media_type.value)
    cache: dict = app.state.search_cache
    hit = cache.get(key)
    if hit is not None and hit[0] > time.monotonic():
        results = hit[1]
    else:
        results = await anilist_search(q.strip(), media_type, interactive = True)
        if len(cache) >= _SEARCH_CACHE_MAX:
            cache.clear()
        cache[key] = (time.monotonic() + _SEARCH_TTL, results)
    for m in results:
        app.state.search_media[m.id] = m

    entries = app.state.anime_state.entries()
    tracked_ids = {e.anilist_id for e in entries if e.anilist_id}
    tracked_titles = {e.title for e in entries}
    return [
        {
            "id": m.id,
            "title": m.display_title(),
            "type": m.media_type.value.lower(),
            "format": m.format,
            "status": m.status.value,
            "episodes": m.episodes,
            "chapters": m.chapters,
            "next_airing_at": m.next_airing_at,
            "next_airing_episode": m.next_airing_episode,
            "cover_image": m.cover_image,
            "tracked": m.id in tracked_ids or m.display_title() in tracked_titles,
        }
        for m in results
    ]


class AddBody(BaseModel):
    title: str
    type: Literal["anime", "manga"] = "anime"
    submitters: list[str] = []
    anilist_id: int | None = None
    backfill: bool = False


class TitleBody(BaseModel):
    title: str


class UpdateBody(BaseModel):
    title: str
    submitters: list[str]
    search_name: str = ""
    download_dir: str = ""
    check_delay: int = 0
    episode_offset: int = 0


@router.post("/anime/add")
async def add_media(body: AddBody, request: Request):
    app = request.app
    cfg = app.state.cfg
    state = app.state.anime_state
    if body.title in state.titles():
        return {"added": False, "reason": "already tracked"}
    entry = MediaEntry(
        title = body.title,
        media_type = body.type,
        submitters = body.submitters,
        anilist_id = body.anilist_id or 0,
    )
    await state.add(cfg.anime_file, entry)
    known = app.state.search_media.get(entry.anilist_id)
    if known is not None:
        app.state.store.seed(known)

    backfill_error = None
    if body.backfill and body.type == "anime":
        backfill_error = (await app.state.tracker.set_backfill(entry.title)).get("error")
    else:
        app.state.tracker.touch(entry)
        await app.state.tstate.save()
    app.state.store.refresh_soon()
    _logger.info("[%s] Added to watchlist%s.", body.title, " with earlier episodes" if body.backfill else "")
    return {
        "added": True,
        "entry": entry_payload(app, entry, time.time()),
        "warning": backfill_error,
    }


@router.post("/anime/remove")
async def remove_media(body: TitleBody, request: Request):
    app = request.app
    state = app.state.anime_state
    if body.title not in state.titles():
        return {"removed": False, "reason": "not tracked"}
    await state.remove(app.state.cfg.anime_file, body.title)
    app.state.tstate.forget(body.title)
    await app.state.tstate.save()
    _logger.info("[%s] Removed from watchlist.", body.title)
    return {"removed": True}


@router.post("/anime/deprecate")
async def deprecate_media(body: TitleBody, request: Request):
    app = request.app
    state = app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.set_deprecated(app.state.cfg.anime_file, body.title, True)
    return {"ok": True}


@router.post("/anime/reactivate")
async def reactivate_media(body: TitleBody, request: Request):
    app = request.app
    state = app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.set_deprecated(app.state.cfg.anime_file, body.title, False)
    show = app.state.tstate.peek(body.title)
    if show is not None:
        # Pick up from here rather than chasing everything that aired while archived.
        show.since = time.time()
        show.completed_at = 0.0
        await app.state.tstate.save()
    app.state.store.refresh_soon()
    return {"ok": True}


@router.post("/anime/update")
@router.post("/anime/submitters")
async def update_entry(body: UpdateBody, request: Request):
    app = request.app
    state = app.state.anime_state
    if body.title not in state.titles():
        return {"ok": False, "reason": "not tracked"}
    await state.update_entry(
        app.state.cfg.anime_file, body.title, body.submitters, body.search_name.strip(),
        body.download_dir.strip(), max(0, body.check_delay), body.episode_offset,
    )
    return {"ok": True, "entry": entry_payload(app, state.get(body.title), time.time())}


@router.post("/anime/backfill")
async def backfill(body: TitleBody, request: Request):
    return await request.app.state.tracker.set_backfill(body.title)


@router.get("/anime/calendar")
async def get_calendar(start: int, end: int, request: Request):
    """Episodes airing between two unix timestamps, for every tracked anime."""
    store = request.app.state.store
    if end <= start:
        return {"episodes": [], "unresolved": []}
    store.refresh_soon()
    payload: dict = {}
    try:
        await store.ensure_window(start, end)
    except AnilistError as exc:
        _logger.warning("Calendar range could not be refreshed: %s", exc)
        payload["error"] = str(exc)
    payload["episodes"] = store.calendar(start, end)
    payload["unresolved"] = store.unresolved_titles()
    return payload
