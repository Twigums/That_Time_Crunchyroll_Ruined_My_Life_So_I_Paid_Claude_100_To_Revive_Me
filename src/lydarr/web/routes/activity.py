"""Activity log: what was added and when, plus releases held for a person to confirm."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

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
    }


@router.post("/activity/approve")
async def approve(body: ApproveBody, request: Request):
    return await request.app.state.tracker.approve(body.title, body.episode, body.hash)


@router.post("/activity/skip")
async def skip(body: EpisodeBody, request: Request):
    return await request.app.state.tracker.skip(body.title, body.episode)


@router.post("/activity/retry")
async def retry(body: EpisodeBody, request: Request):
    return await request.app.state.tracker.retry(body.title, body.episode)
