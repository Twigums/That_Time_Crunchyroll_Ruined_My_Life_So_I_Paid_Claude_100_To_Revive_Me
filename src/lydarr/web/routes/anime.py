"""Anime and manga watchlist management routes."""
import asyncio
import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from anilist.search import search as anilist_search, find_by_title
from anilist.types import MediaType
from lydarr.file_manager import MediaEntry
from lydarr.tracker import _safe_dirname
from lydarr.web.routes.daemon import _daemon_running, spawn_tracker

_logger = logging.getLogger("lydarr.web")

router = APIRouter(prefix = "/api")

# Air times and status change rarely, so cache lookups to avoid hammering the
# AniList API when several clients load the watchlist at once (each entry would
# otherwise trigger a fresh request on every page load, per client).
_STATUS_TTL = 600.0


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
    await state.update_entry(cfg.anime_file, body.title, body.submitters, body.search_name, body.download_dir)
    return {"ok": True}


async def _resolve_status(title: str, media_type: MediaType) -> dict:
    info = await find_by_title(title, media_type)
    if info is None:
        return {"status": None, "next_airing_at": None, "next_airing_episode": None}
    return {
        "status": info.status.value,
        "next_airing_at": info.next_airing_at,
        "next_airing_episode": info.next_airing_episode,
    }


async def _cached_status(app, title: str, media_type: MediaType) -> dict:
    key = (title, media_type.value)
    cache = app.state.status_cache
    inflight = app.state.status_inflight

    async with app.state.status_lock:
        hit = cache.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        # Single-flight: concurrent requests for the same title share one lookup.
        task = inflight.get(key)
        owner = task is None
        if owner:
            task = asyncio.create_task(_resolve_status(title, media_type))
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
        cache[key] = (time.monotonic() + _STATUS_TTL, payload)
    return payload


@router.get("/anime/status")
async def get_status(title: str, request: Request, type: str = "anime"):
    media_type = MediaType.MANGA if type == "manga" else MediaType.ANIME
    return await _cached_status(request.app, title, media_type)
