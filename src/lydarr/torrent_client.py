"""Transmission JSON-RPC client."""
import asyncio
import logging

import httpx

_logger = logging.getLogger(__name__)

POLL_INTERVAL = 5

_session_id = "0"
_session_lock = asyncio.Lock()


async def _rpc(url: str, user: str | None, password: str | None, method: str, arguments: dict | None = None) -> httpx.Response:
    global _session_id
    payload = {"method": method, "arguments": arguments or {}}
    auth = (user, password) if user else None
    async with _session_lock:
        sid = _session_id
    async with httpx.AsyncClient() as client:
        r = await client.post(
            url, json = payload,
            headers = {"X-Transmission-Session-Id": sid},
            auth = auth,
            timeout = 10,
        )
        if r.status_code == 409:
            sid = r.headers.get("X-Transmission-Session-Id", "0")
            async with _session_lock:
                _session_id = sid
            r = await client.post(
                url, json = payload,
                headers = {"X-Transmission-Session-Id": sid},
                auth = auth,
                timeout = 10,
            )
    return r


async def is_client_up(url: str, user: str | None, password: str | None) -> bool:
    try:
        r = await _rpc(url, user, password, "session-get")
        return r.status_code in (200, 401, 409)
    except Exception:
        return False


async def wait_for_client(url: str, user: str | None, password: str | None) -> None:
    _logger.info("Waiting for Transmission...")
    while not await is_client_up(url, user, password):
        _logger.info("Transmission not reachable at %s. Retrying in %ds.", url, POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)
    _logger.info("Transmission is up.")


async def add_magnet(url: str, user: str | None, password: str | None, magnet: str, download_dir: str | None = None) -> int | None:
    args: dict = {"filename": magnet}
    if download_dir:
        args["download-dir"] = download_dir
    try:
        r = await _rpc(url, user, password, "torrent-add", args)
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("result", "") not in ("success", "duplicate torrent"):
            return None
        added = body.get("arguments", {})
        info = added.get("torrent-added") or added.get("torrent-duplicate")
        return info["id"] if info else None
    except Exception as exc:
        _logger.error("add_magnet error: %s", exc)
        return None


async def remove_when_done(url: str, user: str | None, password: str | None, torrent_id: int) -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            r = await _rpc(url, user, password, "torrent-get", {
                "ids": [torrent_id],
                "fields": ["id", "percentDone"],
            })
            if r.status_code != 200:
                continue
            torrents = r.json().get("arguments", {}).get("torrents", [])
            if not torrents:
                return
            if torrents[0].get("percentDone", 0) >= 1.0:
                await _rpc(url, user, password, "torrent-remove", {
                    "ids": [torrent_id],
                    "delete-local-data": False,
                })
                _logger.info("Removed torrent %d from Transmission after download completed.", torrent_id)
                return
        except Exception as exc:
            _logger.error("remove_when_done error for torrent %d: %r", torrent_id, exc)
