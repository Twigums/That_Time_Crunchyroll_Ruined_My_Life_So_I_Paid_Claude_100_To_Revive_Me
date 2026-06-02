"""Per-title async tracking coroutines for anime and manga."""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from anilist.search import find_by_title
from anilist.types import MediaType, MediaStatus
from lydarr.config import AppConfig
from lydarr.file_manager import MediaState, MediaEntry
from lydarr.nyaa_search import search_episode, search_chapter
from lydarr.torrent_client import add_magnet, remove_when_done

_WARN_INTERVAL = 48

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00]')

_logger = logging.getLogger("lydarr.tracker")


def _safe_dirname(name: str) -> str:
    return _UNSAFE_CHARS.sub('', name).strip() or "Unknown"


def _log(name: str, msg: str) -> None:
    _logger.info("[%s] %s", name, msg)



def _pad(n: int) -> str:
    return f"{n:02d}"


async def _sleep_until(target: datetime) -> None:
    delta = (target - datetime.now(tz = timezone.utc)).total_seconds()
    if delta > 0:
        await asyncio.sleep(delta)


async def _add_magnet(cfg: AppConfig, name: str, magnet: str, label: str, download_dir: str | None = None) -> None:
    if download_dir:
        try:
            os.makedirs(download_dir, exist_ok=True)
        except OSError as e:
            _log(name, f"Could not create download dir {download_dir}: {e}")
            download_dir = None
    torrent_id = await add_magnet(cfg.transmission_url, cfg.transmission_user,
                                  cfg.transmission_pass, magnet, download_dir)
    if torrent_id is not None:
        _log(name, f"Added {label} to Transmission -> {download_dir or cfg.default_dir}")
        asyncio.create_task(remove_when_done(
            cfg.transmission_url, cfg.transmission_user, cfg.transmission_pass, torrent_id,
        ))
    else:
        _log(name, f"Transmission error for {label}.")


async def _wait_and_add_episode(
    cfg: AppConfig, name: str, ep_num: int, submitters: list[str], download_dir: str | None = None
) -> None:
    attempts = 0
    while True:
        magnet = await search_episode(name, ep_num, submitters)
        if magnet is None:
            if attempts == 0:
                _log(name, f"No sub for ep {_pad(ep_num)}. Retrying in 30 min.")
            elif attempts % _WARN_INTERVAL == 0:
                _log(name, f"Still no sub for ep {_pad(ep_num)} after {attempts // 2}h")
            await asyncio.sleep(30 * 60)
            attempts += 1
        else:
            await _add_magnet(cfg, name, magnet, f"ep {_pad(ep_num)}", download_dir)
            return


async def _download_all_episodes(
    cfg: AppConfig, name: str, total: int, submitters: list[str], download_dir: str | None = None
) -> None:
    await asyncio.gather(
        *(_wait_and_add_episode(cfg, name, ep, submitters, download_dir) for ep in range(1, total + 1))
    )


async def _step_anime(cfg: AppConfig, state: MediaState, entry: MediaEntry) -> bool:
    name = entry.title
    nyaa_name = entry.search_name or entry.title
    info = await find_by_title(name, MediaType.ANIME)
    if info is None:
        _log(name, "Not found on AniList. Retrying in 6h.")
        await asyncio.sleep(6 * 3600)
        return True

    download_dir = entry.download_dir or os.path.join(cfg.default_dir, _safe_dirname(info.display_title()))
    _log(name, f"Will save to: {download_dir}")

    match info.status:
        case MediaStatus.FINISHED:
            total = info.episodes or 0
            if total == 0:
                _log(name, "Finished but episode count unknown. Checking in 6h.")
                await asyncio.sleep(6 * 3600)
                return True
            _log(name, f"Finished. Downloading eps 1..{total}.")
            await _download_all_episodes(cfg, nyaa_name, total, entry.submitters, download_dir)
            await state.remove(cfg.anime_file, name)
            _log(name, "Done. Removed from anime.toml.")
            return False

        case MediaStatus.RELEASING:
            if info.next_airing_at is None or info.next_airing_episode is None:
                _log(name, "No next episode scheduled. Checking in 6h.")
                await asyncio.sleep(6 * 3600)
                return True
            ep_num = info.next_airing_episode
            air_time = datetime.fromtimestamp(info.next_airing_at, tz = timezone.utc)
            trigger = air_time + timedelta(minutes = 30)
            _log(name, f"Next ep {_pad(ep_num)} airs at {air_time}. Searching at {trigger}.")
            await _sleep_until(trigger)
            await _wait_and_add_episode(cfg, nyaa_name, ep_num, entry.submitters, download_dir)
            return True

        case MediaStatus.NOT_YET_RELEASED | MediaStatus.HIATUS:
            _log(name, f"Status: {info.status.value}. Checking in 6h.")
            await asyncio.sleep(6 * 3600)
            return True

        case _:
            _log(name, f"Status: {info.status.value}. Checking in 1h.")
            await asyncio.sleep(3600)
            return True


async def _wait_and_add_chapter(
    cfg: AppConfig, name: str, chapter_num: int, submitters: list[str], download_dir: str | None = None
) -> None:
    attempts = 0
    while True:
        magnet = await search_chapter(name, chapter_num, submitters)
        if magnet is None:
            if attempts == 0:
                _log(name, f"No scanlation for ch.{chapter_num:03d}. Retrying in 30 min.")
            elif attempts % _WARN_INTERVAL == 0:
                _log(name, f"Still no scanlation for ch.{chapter_num:03d} after {attempts // 2}h")
            await asyncio.sleep(30 * 60)
            attempts += 1
        else:
            await _add_magnet(cfg, name, magnet, f"ch.{chapter_num:03d}", download_dir)
            return


async def _step_manga(cfg: AppConfig, state: MediaState, entry: MediaEntry) -> bool:
    name = entry.title
    nyaa_name = entry.search_name or entry.title
    info = await find_by_title(name, MediaType.MANGA)
    if info is None:
        _log(name, "Not found on AniList. Retrying in 6h.")
        await asyncio.sleep(6 * 3600)
        return True

    total = info.chapters or 0
    download_dir = entry.download_dir or os.path.join(cfg.default_dir, _safe_dirname(info.display_title()))

    if info.status == MediaStatus.FINISHED and total > 0:
        new_chs = list(range(entry.last_chapter + 1, total + 1))
        if new_chs:
            _log(name, f"Finished ({total} ch). Downloading ch.{new_chs[0]:03d}..{new_chs[-1]:03d}.")
            for ch in new_chs:
                await _wait_and_add_chapter(cfg, nyaa_name, ch, entry.submitters, download_dir)
                await state.update_last_chapter(cfg.anime_file, name, ch)
        await state.remove(cfg.anime_file, name)
        _log(name, "Done. Removed from anime.toml.")
        return False

    elif info.status in (MediaStatus.RELEASING, MediaStatus.HIATUS):
        new_chs = list(range(entry.last_chapter + 1, total + 1))
        if new_chs:
            _log(name, f"New chapters: ch.{new_chs[0]:03d}..{new_chs[-1]:03d}.")
            for ch in new_chs:
                await _wait_and_add_chapter(cfg, nyaa_name, ch, entry.submitters, download_dir)
                await state.update_last_chapter(cfg.anime_file, name, ch)
        else:
            _log(name, f"No new chapters (last: ch.{entry.last_chapter:03d}). Checking in 24h.")
        await asyncio.sleep(24 * 3600)
        return True

    else:
        _log(name, f"Status: {info.status.value}. Checking in 12h.")
        await asyncio.sleep(12 * 3600)
        return True


async def track_media(cfg: AppConfig, state: MediaState, entry: MediaEntry) -> None:
    step = _step_manga if entry.media_type == "manga" else _step_anime
    while True:
        current = state.get(entry.title) or entry
        try:
            if not await step(cfg, state, current):
                return
        except Exception as exc:
            _log(entry.title, f"Unexpected error: {exc}. Retrying in 1h.")
            await asyncio.sleep(3600)
