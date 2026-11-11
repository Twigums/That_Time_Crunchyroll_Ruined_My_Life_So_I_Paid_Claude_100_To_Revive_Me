"""Tracker control and Transmission status routes."""
import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from lydarr.config import AppConfig
from lydarr.torrent_client import is_client_up

_log = logging.getLogger("lydarr.web")

router = APIRouter(prefix = "/api")

_STATUS_TIMEOUT = 3
_STOP_WAIT = 20
_START_WAIT = 25


def tracker_running(app) -> bool:
    task = app.state.daemon_task
    return task is not None and not task.done()


def start_tracker(app) -> bool:
    if tracker_running(app):
        return False
    app.state.daemon_task = asyncio.create_task(app.state.tracker.run())
    app.state.daemon_started_at = datetime.now(tz = timezone.utc)
    return True


async def stop_tracker(app) -> bool:
    task = app.state.daemon_task
    app.state.daemon_task = None
    if task is None or task.done():
        return False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _transmission_up(cfg: AppConfig, timeout: float = 10) -> bool:
    return await is_client_up(cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass, timeout = timeout)


async def _systemctl(cfg: AppConfig, action: str) -> tuple[int, str]:
    env = dict(os.environ)
    if cfg.transmission_scope == "user":
        cmd = ["systemctl", "--user", action, cfg.transmission_unit]
        # systemd --user needs a session bus; absent when lydarr runs outside a login session
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    else:
        cmd = ["sudo", "-n", "systemctl", action, cfg.transmission_unit]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout = asyncio.subprocess.DEVNULL,
            stderr = asyncio.subprocess.PIPE,
            env = env,
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    _, stderr = await proc.communicate()
    return proc.returncode or 0, stderr.decode(errors = "replace").strip()


async def _start_transmission(cfg: AppConfig) -> dict:
    if await _transmission_up(cfg):
        return {"ok": True, "started": False}
    rc, err = await _systemctl(cfg, "start")
    if rc != 0:
        _log.error("Transmission start failed (rc=%d): %s", rc, err)
        return {"ok": False, "reason": err or "systemctl start failed"}
    for _ in range(_START_WAIT):
        await _pause(1)
        if await _transmission_up(cfg):
            _log.info("Transmission started.")
            return {"ok": True, "started": True}
    _log.warning("Transmission start timed out.")
    return {"ok": False, "reason": "Transmission did not come up in time"}


async def _stop_transmission(cfg: AppConfig) -> dict:
    if not await _transmission_up(cfg):
        return {"ok": True, "stopped": False}
    rc, err = await _systemctl(cfg, "stop")
    if rc != 0:
        _log.error("Transmission stop failed (rc=%d): %s", rc, err)
        return {"ok": False, "reason": err or "systemctl stop failed"}
    for _ in range(_STOP_WAIT):
        await _pause(1)
        if not await _transmission_up(cfg):
            _log.info("Transmission stopped.")
            return {"ok": True, "stopped": True}
    _log.warning("Transmission stop timed out.")
    return {"ok": False, "reason": "Transmission did not stop in time"}


def _tracker_payload(app) -> dict:
    started_at = app.state.daemon_started_at
    running = tracker_running(app)
    return {
        "running": running,
        "started_at": started_at.isoformat() if started_at and running else None,
    }


@router.get("/status")
async def status(request: Request):
    """Everything the header needs in one call."""
    app = request.app
    return {
        "tracker": _tracker_payload(app),
        "transmission": {"online": await _transmission_up(app.state.cfg, _STATUS_TIMEOUT)},
        "review": len(app.state.tstate.reviews()),
    }


@router.get("/daemon/status")
async def daemon_status(request: Request):
    return {
        **_tracker_payload(request.app),
        "tracking": request.app.state.anime_state.active_titles(),
    }


@router.post("/daemon/start")
async def daemon_start(request: Request):
    app = request.app
    if not start_tracker(app):
        return {"ok": False, "reason": "already running"}
    app.state.tstate.enabled = True
    await app.state.tstate.save()
    return {"ok": True}


@router.post("/daemon/stop")
async def daemon_stop(request: Request):
    app = request.app
    stopped = await stop_tracker(app)
    app.state.tstate.enabled = False
    await app.state.tstate.save()
    if not stopped:
        return {"ok": False, "reason": "not running"}
    return {"ok": True}


@router.get("/transmission/status")
@router.get("/rtorrent/status")
async def transmission_status(request: Request):
    return {"online": await _transmission_up(request.app.state.cfg, _STATUS_TIMEOUT)}


@router.post("/transmission/start")
async def transmission_start(request: Request):
    return await _start_transmission(request.app.state.cfg)


@router.post("/transmission/stop")
async def transmission_stop(request: Request):
    return await _stop_transmission(request.app.state.cfg)


@router.post("/power/start")
async def power_start(request: Request):
    """Bring up Transmission, then the tracker: the order the tracker needs."""
    app = request.app
    transmission = await _start_transmission(app.state.cfg)
    start_tracker(app)
    app.state.tstate.enabled = True
    await app.state.tstate.save()
    return {
        "ok": transmission["ok"],
        "reason": transmission.get("reason"),
        "tracker": _tracker_payload(app),
        "transmission": transmission,
    }


@router.post("/power/stop")
async def power_stop(request: Request):
    """Stop the tracker, then Transmission."""
    app = request.app
    await stop_tracker(app)
    app.state.tstate.enabled = False
    await app.state.tstate.save()
    transmission = await _stop_transmission(app.state.cfg)
    return {
        "ok": transmission["ok"],
        "reason": transmission.get("reason"),
        "tracker": _tracker_payload(app),
        "transmission": transmission,
    }
