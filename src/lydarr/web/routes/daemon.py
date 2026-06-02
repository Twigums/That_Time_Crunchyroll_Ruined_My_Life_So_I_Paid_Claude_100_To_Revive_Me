"""Daemon control and Transmission status routes."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from lydarr.torrent_client import is_client_up
from lydarr.tracker import track_media

_log = logging.getLogger(__name__)

router = APIRouter(prefix = "/api")


async def _run_daemon(cfg, state) -> None:
    active = [e for e in state.entries() if not e.deprecated]
    await asyncio.gather(*(track_media(cfg, state, entry) for entry in active))


def _daemon_running(app) -> bool:
    task = app.state.daemon_task
    return task is not None and not task.done()


def spawn_tracker(app, entry) -> None:
    task = asyncio.create_task(track_media(app.state.cfg, app.state.anime_state, entry))
    app.state.tracker_tasks.add(task)
    task.add_done_callback(app.state.tracker_tasks.discard)


@router.get("/daemon/status")
async def daemon_status(request: Request):
    started_at = request.app.state.daemon_started_at
    running = _daemon_running(request.app)
    return {
        "running": running,
        "tracking": request.app.state.anime_state.active_titles(),
        "started_at": started_at.isoformat() if started_at else None,
    }


@router.post("/daemon/start")
async def daemon_start(request: Request):
    if _daemon_running(request.app):
        return {"ok": False, "reason": "already running"}
    cfg = request.app.state.cfg
    state = request.app.state.anime_state
    if not any(not e.deprecated for e in state.entries()):
        return {"ok": False, "reason": "no active entries in watchlist"}
    request.app.state.daemon_task = asyncio.create_task(_run_daemon(cfg, state))
    request.app.state.daemon_started_at = datetime.now(tz = timezone.utc)
    return {"ok": True}


@router.post("/daemon/stop")
async def daemon_stop(request: Request):
    task = request.app.state.daemon_task
    extra = list(request.app.state.tracker_tasks)
    if (task is None or task.done()) and not extra:
        return {"ok": False, "reason": "not running"}
    for t in extra:
        t.cancel()
    if task is not None:
        task.cancel()
    for t in extra + ([task] if task is not None else []):
        try:
            await t
        except asyncio.CancelledError:
            pass
    request.app.state.tracker_tasks.clear()
    request.app.state.daemon_task = None
    return {"ok": True}


@router.get("/rtorrent/status")
async def rtorrent_status(request: Request):
    cfg = request.app.state.cfg
    online = await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass)
    return {"online": online}


@router.post("/transmission/stop")
async def transmission_stop(request: Request):
    cfg = request.app.state.cfg
    if not await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass):
        return {"ok": True, "stopped": False}
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "stop", "transmission-daemon",
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.DEVNULL,
        )
        asyncio.create_task(proc.wait())
    except Exception as exc:
        _log.error("transmission stop failed: %s", exc)
        return {"ok": False, "reason": "internal error"}
    for _ in range(20):
        await asyncio.sleep(1)
        if not await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass):
            _log.info("Transmission stopped.")
            return {"ok": True, "stopped": True}
    _log.warning("Transmission stop timed out.")
    return {"ok": False, "reason": "timed out"}


@router.post("/transmission/start")
async def transmission_start(request: Request):
    cfg = request.app.state.cfg
    if await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass):
        return {"ok": True, "started": False}
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "start", "transmission-daemon",
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.DEVNULL,
        )
        asyncio.create_task(proc.wait())
    except FileNotFoundError:
        return {"ok": False, "reason": "systemctl not found"}
    except Exception as exc:
        _log.error("transmission start failed: %s", exc)
        return {"ok": False, "reason": "internal error"}
    for _ in range(25):
        await asyncio.sleep(1)
        if await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass):
            _log.info("Transmission started.")
            return {"ok": True, "started": True}
    _log.warning("Transmission start timed out.")
    return {"ok": False, "reason": "timed out"}
