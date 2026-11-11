"""Transmission JSON-RPC client."""
import asyncio
import logging
from dataclasses import dataclass

import httpx

_logger = logging.getLogger(__name__)

POLL_INTERVAL = 5

_session_id = "0"
_session_lock = asyncio.Lock()


@dataclass(frozen = True)
class AddedTorrent:
    id: int
    hash: str
    name: str
    duplicate: bool = False


async def _rpc(url: str, user: str | None, password: str | None, method: str,
               arguments: dict | None = None, timeout: float = 10) -> httpx.Response:
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
            timeout = timeout,
        )
        if r.status_code == 409:
            sid = r.headers.get("X-Transmission-Session-Id", "0")
            async with _session_lock:
                _session_id = sid
            r = await client.post(
                url, json = payload,
                headers = {"X-Transmission-Session-Id": sid},
                auth = auth,
                timeout = timeout,
            )
    return r


async def is_client_up(url: str, user: str | None, password: str | None, timeout: float = 10) -> bool:
    try:
        r = await _rpc(url, user, password, "session-get", timeout = timeout)
        return r.status_code in (200, 401, 409)
    except Exception:
        return False


async def wait_for_client(url: str, user: str | None, password: str | None) -> None:
    _logger.info("Waiting for Transmission...")
    while not await is_client_up(url, user, password):
        _logger.info("Transmission not reachable at %s. Retrying in %ds.", url, POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)
    _logger.info("Transmission is up.")


async def add_magnet(url: str, user: str | None, password: str | None, magnet: str,
                     download_dir: str | None = None) -> AddedTorrent | None:
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
        if not info:
            return None
        return AddedTorrent(
            id = info.get("id", 0),
            hash = str(info.get("hashString", "")).lower(),
            name = info.get("name", ""),
            duplicate = "torrent-duplicate" in added,
        )
    except Exception as exc:
        _logger.error("add_magnet error: %r", exc)
        return None


async def torrent_progress(url: str, user: str | None, password: str | None,
                           hashes: list[str]) -> dict[str, float] | None:
    """Fraction complete for each of the given torrents Transmission still has; None if unreachable.

    Torrents are addressed by info hash: Transmission's numeric ids are reassigned
    every time it restarts, so an id remembered from earlier may name another torrent.
    """
    if not hashes:
        return {}
    try:
        r = await _rpc(url, user, password, "torrent-get", {
            "ids": list(hashes),
            "fields": ["hashString", "percentDone"],
        })
        if r.status_code != 200:
            return None
        torrents = r.json().get("arguments", {}).get("torrents", [])
        return {str(t.get("hashString", "")).lower(): float(t.get("percentDone", 0)) for t in torrents}
    except Exception:
        return None


async def remove_torrents(url: str, user: str | None, password: str | None, hashes: list[str]) -> bool:
    """Drop torrents from Transmission's list, keeping the downloaded files."""
    if not hashes:
        return True
    try:
        r = await _rpc(url, user, password, "torrent-remove", {
            "ids": list(hashes),
            "delete-local-data": False,
        })
        return r.status_code == 200
    except Exception:
        return False
