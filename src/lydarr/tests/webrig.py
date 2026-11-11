"""Shared setup for the web route tests: a real app over in-memory AniList data."""
from datetime import datetime, timezone
from email.utils import format_datetime

from anilist.types import AnilistMedia, MediaStatus, MediaType
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry, MediaState
from lydarr.media_store import MediaStore
from lydarr.state import TrackerState
from lydarr.tracker import Tracker
from lydarr.web.app import create_app
from nyaa.types import Torrent, TorrentType


def make_cfg(tmp_path, **overrides) -> AppConfig:
    base = dict(
        anime_file = str(tmp_path / "media.toml"),
        transmission_url = "http://transmission.invalid/rpc",
        transmission_user = None,
        transmission_pass = None,
        default_dir = str(tmp_path / "library"),
        default_dir_set = True,
        lydarr_user = None,
        lydarr_pass = None,
    )
    base.update(overrides)
    return AppConfig(**base)


def make_media(media_id: int, english: str | None, romaji: str | None = None, *,
               status: MediaStatus = MediaStatus.RELEASING, media_type: MediaType = MediaType.ANIME,
               **extra) -> AnilistMedia:
    return AnilistMedia(
        id = media_id, title_english = english, title_romaji = romaji or english, media_type = media_type,
        status = status, episodes = extra.pop("episodes", 12), chapters = extra.pop("chapters", None),
        next_airing_at = None, next_airing_episode = None, **extra,
    )


def make_torrent(name: str, published: int | None = None, number: int = 1, seeders: int = 847) -> Torrent:
    date = format_datetime(datetime.fromtimestamp(published, tz = timezone.utc)) if published else "2025-03-29"
    return Torrent(
        id = str(number),
        category = "Anime - English-translated",
        url = f"https://nyaa.si/view/{number}",
        name = name,
        download_url = f"https://nyaa.si/download/{number}.torrent",
        magnet = f"magnet:?xt=urn:btih:{number:040x}",
        size = "1.37 GiB",
        date = date,
        seeders = seeders,
        leechers = 23,
        completed = None,
        type = TorrentType.TRUSTED,
    )


class WebRig:
    def __init__(self, tmp_path, entries: list[MediaEntry] | None = None, *,
                 media: list[AnilistMedia] | None = None, schedule: dict[int, dict[int, int]] | None = None,
                 cfg: AppConfig | None = None, resume_tracker: bool = False) -> None:
        self.cfg = cfg or make_cfg(tmp_path)
        self.state = MediaState(entries or [])
        self.store = MediaStore(self.cfg.cache_file, self.state, self.cfg.anime_file)
        for m in media or []:
            self.store._media[m.id] = m
        self.store._schedule = {k: dict(v) for k, v in (schedule or {}).items()}
        self.store._scheduled_ids = set(self.store._schedule)
        self.store._prequels = {m.id: [] for m in media or []}
        self.refreshes: list[bool] = []
        self.store.refresh_soon = self._refresh_soon
        self.tstate = TrackerState(self.cfg.state_file)
        self.tracker = Tracker(self.cfg, self.state, self.store, self.tstate)
        self.app = create_app(
            self.cfg, self.state, store = self.store, tstate = self.tstate, tracker = self.tracker,
            resume_tracker = resume_tracker,
        )

    def _refresh_soon(self, force: bool = False):
        self.refreshes.append(force)
        return None
