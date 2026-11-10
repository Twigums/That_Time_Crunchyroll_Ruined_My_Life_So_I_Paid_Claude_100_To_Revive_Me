import pytest

from anilist.types import AnilistMedia, MediaStatus, MediaType
from lydarr.config import AppConfig, load_config
from lydarr.file_manager import MediaEntry, MediaState, WatchlistError, read_entries
from lydarr.paths import default_download_dir, resolve_download_dir, safe_dirname


def _cfg(tmp_path) -> AppConfig:
    return AppConfig(
        anime_file = str(tmp_path / "anime.toml"),
        transmission_url = "http://localhost:9091/transmission/rpc",
        transmission_user = None,
        transmission_pass = None,
        default_dir = "/media/Anime",
        default_dir_set = True,
        lydarr_user = None,
        lydarr_pass = None,
    )


def _info(english: str | None, romaji: str) -> AnilistMedia:
    return AnilistMedia(
        id = 1, title_english = english, title_romaji = romaji, media_type = MediaType.ANIME,
        status = MediaStatus.RELEASING, episodes = 12, chapters = None,
        next_airing_at = None, next_airing_episode = None,
    )


def test_safe_dirname_strips_path_characters():
    assert safe_dirname('Re:ZERO -Starting Life in Another World-') == "ReZERO -Starting Life in Another World-"
    assert safe_dirname("Gals Can't Be Kind to Otaku!?") == "Gals Can't Be Kind to Otaku!"
    assert safe_dirname("///") == "Unknown"


def test_entry_path_overrides_everything(tmp_path):
    entry = MediaEntry(title = "Show", download_dir = "/media/Anime/Show/Season 2")
    assert resolve_download_dir(_cfg(tmp_path), entry, _info("Show EN", "Show")) == "/media/Anime/Show/Season 2"


def test_default_folder_uses_anilist_display_title(tmp_path):
    entry = MediaEntry(title = "FX Senshi Kurumi-chan")
    info = _info("FX Fighter Kurumi-chan", "FX Senshi Kurumi-chan")
    assert resolve_download_dir(_cfg(tmp_path), entry, info) == "/media/Anime/FX Fighter Kurumi-chan"


def test_default_folder_falls_back_to_entry_title(tmp_path):
    entry = MediaEntry(title = "Show: Part 2")
    assert default_download_dir(_cfg(tmp_path), entry, None) == "/media/Anime/Show Part 2"


def test_state_and_cache_files_default_next_to_watchlist(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.state_file == str(tmp_path / "lydarr-state.json")
    assert cfg.cache_file == str(tmp_path / "lydarr-cache.json")


def test_load_config_reads_matching_preferences(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIME_FILE", str(tmp_path / "anime.toml"))
    monkeypatch.setenv("LYDARR_FALLBACK_HOURS", "4.5")
    monkeypatch.setenv("LYDARR_HEVC_WAIT_HOURS", "0")
    monkeypatch.setenv("LYDARR_PREFER_HEVC", "0")
    monkeypatch.setenv("LYDARR_RESOLUTION", "720")
    monkeypatch.setenv("LYDARR_STATE_FILE", str(tmp_path / "s.json"))
    monkeypatch.setenv("LYDARR_TRANSMISSION_SCOPE", "system")

    cfg = load_config()

    assert cfg.fallback_hours == 4.5
    assert cfg.hevc_wait_hours == 0
    assert cfg.prefer_hevc is False
    assert cfg.resolution == 720
    assert cfg.state_file == str(tmp_path / "s.json")
    assert cfg.transmission_scope == "system"


def test_load_config_ignores_garbage_numbers(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIME_FILE", str(tmp_path / "anime.toml"))
    monkeypatch.setenv("LYDARR_FALLBACK_HOURS", "soon")
    monkeypatch.setenv("LYDARR_RESOLUTION", "")
    cfg = load_config()
    assert cfg.fallback_hours == 2.0
    assert cfg.resolution == 1080


@pytest.mark.asyncio
async def test_anilist_id_and_offset_round_trip(tmp_path):
    path = str(tmp_path / "anime.toml")
    state = MediaState([])
    await state.add(path, MediaEntry(title = "Show", anilist_id = 42))
    await state.update_entry(path, "Show", ["SubsPlease"], "", episode_offset = 48)

    [entry] = read_entries(path)
    assert entry.anilist_id == 42
    assert entry.episode_offset == 48
    assert entry.submitters == ["SubsPlease"]


def test_unset_id_and_offset_are_not_written(tmp_path):
    path = tmp_path / "anime.toml"
    path.write_text('[[media]]\ntitle = "Show"\n')
    [entry] = read_entries(str(path))
    assert entry.anilist_id == 0
    assert entry.episode_offset == 0


def test_invalid_toml_raises_a_readable_error(tmp_path):
    path = tmp_path / "anime.toml"
    path.write_text('[[media]]\ntitle = "Show\n')
    with pytest.raises(WatchlistError, match = "not valid TOML"):
        read_entries(str(path))


def test_block_without_title_names_its_position(tmp_path):
    path = tmp_path / "anime.toml"
    path.write_text('[[media]]\ntitle = "A"\n\n[[media]]\ntype = "anime"\n')
    with pytest.raises(WatchlistError, match = "#2"):
        read_entries(str(path))
