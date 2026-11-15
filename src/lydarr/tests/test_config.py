import pytest
from pathlib import Path
from unittest.mock import patch
from lydarr.config import load_config, AppConfig


def test_load_config_defaults():
    with patch.dict("os.environ", {}, clear = True):
        cfg = load_config()

    assert cfg.anime_file == "anime.toml"
    assert cfg.transmission_url == "http://localhost:9091/transmission/rpc"
    assert cfg.transmission_user is None
    assert cfg.transmission_pass is None
    assert cfg.default_dir == str(Path.home() / "Downloads")
    assert cfg.default_dir_set is False


def test_load_config_from_env():
    env = {
        "ANIME_FILE": "/custom/watchlist.toml",
        "TRANSMISSION_URL": "http://myserver:9091/transmission/rpc",
        "TRANSMISSION_USER": "admin",
        "TRANSMISSION_PASS": "secret",
        "LYDARR_DEFAULT_DIR": "/media/downloads",
    }
    with patch.dict("os.environ", env, clear = True):
        cfg = load_config()

    assert cfg.anime_file == "/custom/watchlist.toml"
    assert cfg.transmission_url == "http://myserver:9091/transmission/rpc"
    assert cfg.transmission_user == "admin"
    assert cfg.transmission_pass == "secret"
    assert cfg.default_dir == "/media/downloads"
    assert cfg.default_dir_set is True


def test_load_config_empty_user_becomes_none():
    with patch.dict("os.environ", {"TRANSMISSION_USER": "", "TRANSMISSION_PASS": ""}, clear = False):
        cfg = load_config()

    assert cfg.transmission_user is None
    assert cfg.transmission_pass is None


def test_load_config_default_dir_not_set():
    with patch.dict("os.environ", {}, clear = True):
        cfg = load_config()

    assert cfg.default_dir_set is False
    assert "Downloads" in cfg.default_dir


def test_app_config_is_dataclass():
    cfg = AppConfig(
        anime_file = "test.toml",
        transmission_url = "http://localhost:9091/transmission/rpc",
        transmission_user = None,
        transmission_pass = None,
        default_dir = "/tmp",
        default_dir_set = False,
        lydarr_user = None,
        lydarr_pass = None,
    )
    assert cfg.anime_file == "test.toml"
    assert cfg.transmission_user is None


def test_jellyfin_is_off_until_both_url_and_key_are_set():
    with patch.dict("os.environ", {}, clear = True):
        cfg = load_config()
    assert (cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_path_map) == ("", "", [])
    assert cfg.jellyfin_enabled is False

    with patch.dict("os.environ", {"JELLYFIN_URL": "http://jellyfin:8096/"}, clear = True):
        assert load_config().jellyfin_enabled is False

    env = {"JELLYFIN_URL": " http://jellyfin:8096/ ", "JELLYFIN_API_KEY": " abc123 "}
    with patch.dict("os.environ", env, clear = True):
        cfg = load_config()
    assert (cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_enabled) == ("http://jellyfin:8096", "abc123", True)


def test_jellyfin_path_map_pairs():
    raw = " ~/media/Anime = /media/Anime ;/mnt/a=/a=b;;junk;=/nothing;/empty= "
    with patch.dict("os.environ", {"JELLYFIN_PATH_MAP": raw}, clear = True):
        cfg = load_config()
    assert cfg.jellyfin_path_map == [
        (str(Path.home() / "media" / "Anime"), "/media/Anime"),
        ("/mnt/a", "/a=b"),
    ]
