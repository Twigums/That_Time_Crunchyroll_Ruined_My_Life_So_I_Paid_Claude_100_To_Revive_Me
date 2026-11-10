"""Save-path resolution shared by the tracker, the web UI and manual adds."""
import os
import re

from anilist.types import AnilistMedia
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00]')


def safe_dirname(name: str) -> str:
    return _UNSAFE_CHARS.sub("", name).strip() or "Unknown"


def default_download_dir(cfg: AppConfig, entry: MediaEntry, info: AnilistMedia | None) -> str:
    folder = info.display_title() if info is not None else entry.title
    return os.path.join(cfg.default_dir, safe_dirname(folder))


def resolve_download_dir(cfg: AppConfig, entry: MediaEntry, info: AnilistMedia | None) -> str:
    """Where an entry's downloads go: its own path if set, else a per-title folder."""
    return entry.download_dir or default_download_dir(cfg, entry, info)
