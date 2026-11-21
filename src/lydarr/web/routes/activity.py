"""Activity log: what was added and when, plus releases held for a person to confirm."""
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from lydarr.jellyfin import JellyfinError
from lydarr.jellyfin import from_config as jellyfin_from_config

_logger = logging.getLogger("lydarr.web")

ADDED = ("grabbed", "approved", "manual")

router = APIRouter(prefix = "/api")


class EpisodeBody(BaseModel):
    title: str
    episode: int


class ApproveBody(EpisodeBody):
    hash: str


@router.get("/activity")
async def activity(request: Request, limit: int = 200):
    app = request.app
    return {
        "review": app.state.tracker.reviews(),
        "events": app.state.tstate.activity(max(1, min(limit, 500))),
        "jellyfin": app.state.cfg.jellyfin_enabled,
    }


@router.get("/activity/{event_id}/jellyfin")
async def open_in_jellyfin(event_id: int, request: Request):
    """Send the browser to the Jellyfin page of what an added torrent became."""
    app = request.app
    jellyfin = jellyfin_from_config(app.state.cfg)
    event = app.state.tstate.event(event_id)
    if jellyfin is None:
        return PlainTextResponse("Jellyfin is not set up: set JELLYFIN_URL and JELLYFIN_API_KEY.", 404)
    if event is None or event.get("kind") not in ADDED or not event.get("dir") or not event.get("name"):
        return PlainTextResponse("No download to look up for that activity entry.", 404)
    try:
        item_id = await jellyfin.find(os.path.join(event["dir"], event["name"]), event.get("episode"))
        if item_id is None:
            return PlainTextResponse(
                f"No Jellyfin library holds {event['dir']}; see JELLYFIN_PATH_MAP.", 404)
        return RedirectResponse(jellyfin.web_link(item_id, await jellyfin.server_id()), 307)
    except JellyfinError as exc:
        _logger.warning("Jellyfin link for activity %d: %s", event_id, exc)
        return PlainTextResponse(f"Jellyfin: {exc}", 502)


@router.post("/activity/approve")
async def approve(body: ApproveBody, request: Request):
    return await request.app.state.tracker.approve(body.title, body.episode, body.hash)


@router.post("/activity/skip")
async def skip(body: EpisodeBody, request: Request):
    return await request.app.state.tracker.skip(body.title, body.episode)


@router.post("/activity/retry")
async def retry(body: EpisodeBody, request: Request):
    return await request.app.state.tracker.retry(body.title, body.episode)
