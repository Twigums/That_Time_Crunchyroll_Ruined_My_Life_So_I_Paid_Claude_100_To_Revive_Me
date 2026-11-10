"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AppConfig:
    anime_file: str
    transmission_url: str
    transmission_user: str | None
    transmission_pass: str | None
    default_dir: str
    default_dir_set: bool
    lydarr_user: str | None
    lydarr_pass: str | None
    state_file: str = ""
    cache_file: str = ""
    transmission_unit: str = "transmission-daemon"
    transmission_scope: str = "user"
    fallback_hours: float = 2.0
    hevc_wait_hours: float = 2.0
    prefer_hevc: bool = True
    resolution: int = 1080
    log_file: str | None = None

    def __post_init__(self) -> None:
        base = os.path.dirname(os.path.abspath(self.anime_file))
        if not self.state_file:
            self.state_file = os.path.join(base, "lydarr-state.json")
        if not self.cache_file:
            self.cache_file = os.path.join(base, "lydarr-cache.json")


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "")))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_path(name: str) -> str:
    raw = os.environ.get(name)
    return str(Path(raw).expanduser()) if raw else ""


def load_config() -> AppConfig:
    anime_file = os.environ.get("ANIME_FILE", "anime.toml")
    transmission_url = os.environ.get("TRANSMISSION_URL", "http://localhost:9091/transmission/rpc")
    transmission_user = os.environ.get("TRANSMISSION_USER") or None
    transmission_pass = os.environ.get("TRANSMISSION_PASS") or None
    default_dir_env = os.environ.get("LYDARR_DEFAULT_DIR")
    default_dir = str(Path(default_dir_env).expanduser()) if default_dir_env else str(Path.home() / "Downloads")
    lydarr_user = os.environ.get("LYDARR_USER") or None
    lydarr_pass = os.environ.get("LYDARR_PASS") or None
    return AppConfig(
        anime_file = anime_file,
        transmission_url = transmission_url,
        transmission_user = transmission_user,
        transmission_pass = transmission_pass,
        default_dir = default_dir,
        default_dir_set = default_dir_env is not None,
        lydarr_user = lydarr_user,
        lydarr_pass = lydarr_pass,
        state_file = _env_path("LYDARR_STATE_FILE"),
        cache_file = _env_path("LYDARR_CACHE_FILE"),
        transmission_unit = os.environ.get("LYDARR_TRANSMISSION_UNIT") or "transmission-daemon",
        transmission_scope = os.environ.get("LYDARR_TRANSMISSION_SCOPE") or "user",
        fallback_hours = _env_float("LYDARR_FALLBACK_HOURS", 2.0),
        hevc_wait_hours = _env_float("LYDARR_HEVC_WAIT_HOURS", 2.0),
        prefer_hevc = _env_flag("LYDARR_PREFER_HEVC", True),
        resolution = _env_int("LYDARR_RESOLUTION", 1080),
        log_file = _env_path("LYDARR_LOG_FILE") or None,
    )
