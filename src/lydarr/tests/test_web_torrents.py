import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from lydarr.config import AppConfig
from lydarr.file_manager import MediaState
from lydarr.web.app import create_app
from nyaa.types import Torrent, TorrentType


def _make_cfg(tmp_path) -> AppConfig:
    return AppConfig(
        anime_file = str(tmp_path / "media.toml"),
        transmission_url = "http://localhost:9091/transmission/rpc",
        transmission_user = None,
        transmission_pass = None,
        default_dir = "/downloads",
        default_dir_set = True,
        lydarr_user = None,
        lydarr_pass = None,
    )


def _make_torrent(name: str = "[SubsPlease] Solo Leveling - 25 (1080p) [0047E2A3].mkv") -> Torrent:
    return Torrent(
        id = "1234567",
        category = "Anime - English-translated",
        url = "https://nyaa.si/view/1234567",
        name = name,
        download_url = "https://nyaa.si/download/1234567.torrent",
        magnet = "magnet:?xt=urn:btih:0047E2A3DEADBEEF0047E2A3DEADBEEF0047E2A3",
        size = "1.37 GiB",
        date = "2025-03-29",
        seeders = 847,
        leechers = 23,
        completed = None,
        type = TorrentType.TRUSTED,
    )


def test_search_torrents_basic(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])
    fake = [_make_torrent()]

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = fake):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=Solo+Leveling")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "1234567"
    assert data[0]["name"] == "[SubsPlease] Solo Leveling - 25 (1080p) [0047E2A3].mkv"
    assert data[0]["seeders"] == 847
    assert data[0]["type"] == "trusted"


def test_search_torrents_with_episode(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])
    fake = [_make_torrent()]
    captured = []

    async def mock_search(site, params):
        captured.append(params.keyword)
        return fake

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, side_effect = mock_search):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=Solo+Leveling&ep=25")

    assert resp.status_code == 200
    assert captured[0] == "Solo Leveling - 25"


def test_search_torrents_hevc_filter(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])
    normal = _make_torrent("[SubsPlease] Solo Leveling - 25 (1080p).mkv")
    hevc = _make_torrent("[SubsPlease] Solo Leveling - 25 (1080p) [HEVC].mkv")

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = [normal, hevc]):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=Solo+Leveling&hevc_only=true")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "HEVC" in data[0]["name"]


def test_search_torrents_site_sukebei(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])
    captured_sites = []

    async def mock_search(site, params):
        captured_sites.append(site)
        return []

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, side_effect = mock_search):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=test&site=sukebei")

    from nyaa.types import TorrentSite
    assert captured_sites[0] == TorrentSite.SUKEBEI_NYAA_SI


def test_search_torrents_unknown_site_defaults_to_nyaa(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])
    captured_sites = []

    async def mock_search(site, params):
        captured_sites.append(site)
        return []

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, side_effect = mock_search):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=test&site=unknown_site")

    from nyaa.types import TorrentSite
    assert captured_sites[0] == TorrentSite.NYAA_SI


def test_add_torrent_success(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])

    with patch("lydarr.web.routes.torrents.add_magnet", new_callable = AsyncMock, return_value = True):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.post(
                "/api/torrents/add",
                json = {"magnet": "magnet:?xt=urn:btih:0047E2A3DEADBEEF0047E2A3DEADBEEF0047E2A3"},
            )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_add_torrent_failure(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])

    with patch("lydarr.web.routes.torrents.add_magnet", new_callable = AsyncMock, return_value = None):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.post(
                "/api/torrents/add",
                json = {"magnet": "magnet:?xt=urn:btih:BADHASH"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


def test_search_torrents_returns_magnet_and_download_url(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = MediaState([])

    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = [_make_torrent()]):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/torrents/search?q=Solo+Leveling")

    data = resp.json()
    assert data[0]["magnet"].startswith("magnet:")
    assert "download_url" in data[0]
