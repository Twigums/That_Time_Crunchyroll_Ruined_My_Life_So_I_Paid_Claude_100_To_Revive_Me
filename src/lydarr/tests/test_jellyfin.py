from unittest.mock import patch

import httpx
import pytest

from lydarr.__main__ import _jellyfin_check, _jellyfin_line
from lydarr.jellyfin import (
    Jellyfin, JellyfinError, Placement, _request as real_request, from_config, inside, to_jellyfin,
)
from lydarr.tests.fake_jellyfin import KEY, SERVER_ID, URL, FakeJellyfin
from lydarr.tests.webrig import make_cfg

SEASON = "/media/Anime/Apothecary Diaries/Season 3"
FILE = f"{SEASON}/[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 07 [1080p].mkv"


# ── paths ────────────────────────────────────────────────────────────────────

def test_inside_means_the_folder_itself_or_below_it():
    assert inside(FILE, "/media/Anime")
    assert inside("/media/Anime/", "/media/Anime")
    assert inside("/media/Anime/x", "/")
    assert not inside("/media/Anime Extra/x", "/media/Anime")
    assert not inside("/media", "/media/Anime")
    assert not inside("/media/x", "")
    assert inside("D:\\Media\\Anime\\Show\\ep.mkv", "D:/Media/Anime")


def test_a_path_already_inside_a_library_is_used_as_it_is():
    assert to_jellyfin(FILE, [], ["/media/Anime"]) == FILE


def test_the_explicit_map_wins_and_its_longest_prefix_first():
    path_map = [("/home/me/media", "/data"), ("/home/me/media/Anime", "/anime")]
    assert to_jellyfin("/home/me/media/Anime/Show/ep.mkv", path_map, ["/anime", "/data"]) == "/anime/Show/ep.mkv"
    assert to_jellyfin("/home/me/media/TV/ep.mkv", path_map, []) == "/data/TV/ep.mkv"
    assert to_jellyfin("/home/me/media", path_map, []) == "/data"
    assert to_jellyfin("/srv/x.mkv", [("/", "/mnt/host/")], []) == "/mnt/host/srv/x.mkv"


def test_folders_mounted_under_another_root_are_lined_up_by_name():
    local = "/home/me/media/Jellyfin/Anime/Apothecary Diaries/Season 3/ep07.mkv"
    locations = ["/media/Jellyfin/TV", "/media/Jellyfin/Anime", "/media/Jellyfin/Movies"]
    assert to_jellyfin(local, [], locations) == "/media/Jellyfin/Anime/Apothecary Diaries/Season 3/ep07.mkv"
    assert to_jellyfin(local, [], ["/data/anime"]) == "/data/anime/Apothecary Diaries/Season 3/ep07.mkv"


def test_the_longest_run_of_matching_names_decides():
    local = "/home/me/Jellyfin/Anime/Show/ep.mkv"
    assert to_jellyfin(local, [], ["/other/Anime", "/media/Jellyfin/Anime"]) == "/media/Jellyfin/Anime/Show/ep.mkv"


def test_no_guess_when_it_is_ambiguous_or_nothing_matches():
    assert to_jellyfin("/home/me/Anime/ep.mkv", [], ["/a/Anime", "/b/Anime"]) is None
    assert to_jellyfin("/home/me/Anime/Anime/ep.mkv", [], ["/media/Anime"]) is None
    assert to_jellyfin("/home/me/Downloads/ep.mkv", [], ["/media/Anime"]) is None


# ── the server ───────────────────────────────────────────────────────────────

def _server() -> tuple[FakeJellyfin, str]:
    jf = FakeJellyfin({"Anime": ["/media/Anime"], "Everything": ["/media"]})
    show = jf.add_show("Anime", "Apothecary Diaries", "/media/Anime/Apothecary Diaries")
    jf.add_show("Anime", "Apothecary", "/media/Anime/Apothecary")
    return jf, show


@pytest.mark.asyncio
async def test_locate_finds_the_deepest_library_and_the_show_holding_the_path():
    jf, show = _server()
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL, KEY)
        place = await client.locate(FILE)
        await client.locate(SEASON)

    assert place.path == FILE
    assert place.library.name == "Anime"
    assert (place.show.id, place.show.name) == (show, "Apothecary Diaries")
    assert [path for _, path, _ in jf.calls] == ["/Library/VirtualFolders", "/Items"]


@pytest.mark.asyncio
async def test_locate_reports_a_path_no_library_holds():
    jf = FakeJellyfin({"Anime": ["/media/Anime"]})
    with patch("lydarr.jellyfin._request", jf):
        assert await Jellyfin(URL, KEY).locate("/elsewhere/ep.mkv") == Placement()
        assert await Jellyfin(URL, KEY, [("/elsewhere", "/mnt")]).locate("/elsewhere/ep.mkv") == Placement("/mnt/ep.mkv")


@pytest.mark.asyncio
async def test_find_picks_the_episode_whose_file_is_the_download():
    jf, show = _server()
    jf.add_episode(show, f"{SEASON}/[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 06 [1080p].mkv", 6)
    wanted = jf.add_episode(show, FILE, 7)
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL, KEY)
        assert await client.find(FILE) == wanted
        assert await client.find(FILE.removesuffix(".mkv")) == wanted


@pytest.mark.asyncio
async def test_find_falls_back_to_a_folder_torrent_then_the_episode_number():
    jf, show = _server()
    in_folder = jf.add_episode(show, f"{SEASON}/[Group] Batch/ep 08.mkv", 8)
    renamed = jf.add_episode(show, f"{SEASON}/Kusuriya 09.mkv", 9)
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL, KEY)
        assert await client.find(f"{SEASON}/[Group] Batch") == in_folder
        assert await client.find(f"{SEASON}/[SubsPlease] Kusuriya - 09 (1080p)", 9) == renamed
        assert await client.find(f"{SEASON}/[SubsPlease] Kusuriya - 10 (1080p)", 10) == show


@pytest.mark.asyncio
async def test_find_gives_the_show_when_several_episodes_fit():
    jf, show = _server()
    jf.add_episode(show, f"{SEASON}/[Group] Batch/ep 01.mkv", 1)
    jf.add_episode(show, f"{SEASON}/[Group] Batch/ep 02.mkv", 2)
    jf.add_episode(show, "/media/Anime/Apothecary Diaries/Season 1/ep 05.mkv", 5)
    jf.add_episode(show, "/media/Anime/Apothecary Diaries/Season 2/ep 05.mkv", 5)
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL, KEY)
        assert await client.find(f"{SEASON}/[Group] Batch", 1) == show
        assert await client.find(f"{SEASON}/new ep 05.mkv", 5) == show


@pytest.mark.asyncio
async def test_find_gives_the_library_for_an_unknown_show_and_nothing_outside_one():
    jf, _ = _server()
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL, KEY)
        assert await client.find("/media/Anime/New Show/ep 01.mkv") == "lib-Anime"
        assert await client.find("/elsewhere/ep 01.mkv") is None


@pytest.mark.asyncio
async def test_web_link_opens_the_item_in_jellyfins_web_client():
    jf, show = _server()
    with patch("lydarr.jellyfin._request", jf):
        client = Jellyfin(URL + "/", KEY)
        link = client.web_link(show, await client.server_id())
    assert link == f"{URL}/web/#/details?id={show}&serverId={SERVER_ID}"


@pytest.mark.asyncio
async def test_refresh_asks_for_a_scan_for_new_and_updated_files():
    jf, show = _server()
    with patch("lydarr.jellyfin._request", jf):
        await Jellyfin(URL, KEY).refresh(show)
    method, path, params = jf.calls[-1]
    assert (method, path) == ("POST", f"/Items/{show}/Refresh")
    assert params["metadataRefreshMode"] == params["imageRefreshMode"] == "Default"
    assert params["replaceAllMetadata"] == params["replaceAllImages"] == "false"
    assert jf.scans == [show]


@pytest.mark.asyncio
@pytest.mark.parametrize("status, says", [
    (401, "JELLYFIN_API_KEY"),
    (403, "admin"),
    (404, "HTTP 404 from /Library/VirtualFolders; check JELLYFIN_URL"),
    (502, "HTTP 502"),
])
async def test_refusals_and_failures_say_what_to_check(status, says):
    jf = FakeJellyfin()
    jf.status = status
    with patch("lydarr.jellyfin._request", jf), pytest.raises(JellyfinError, match = says):
        await Jellyfin(URL, KEY).libraries()


@pytest.mark.asyncio
async def test_an_unreachable_server_is_an_error():
    jf = FakeJellyfin()
    jf.down = True
    with patch("lydarr.jellyfin._request", jf), pytest.raises(JellyfinError, match = "cannot reach"):
        await Jellyfin(URL, KEY).libraries()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["jellyfin.invalid:8096", "http://jellyfin.invalid:port"])
async def test_a_malformed_url_names_the_setting(url):
    with patch("lydarr.jellyfin._request", real_request), pytest.raises(JellyfinError, match = "JELLYFIN_URL"):
        await Jellyfin(url, KEY).libraries()


@pytest.mark.asyncio
async def test_unreadable_replies_are_errors():
    async def garbage(method, url, api_key, params = None):
        return httpx.Response(200, text = "<html>login</html>")
    with patch("lydarr.jellyfin._request", garbage), pytest.raises(JellyfinError, match = "unreadable"):
        await Jellyfin(URL, KEY).libraries()


@pytest.mark.asyncio
async def test_requests_carry_the_api_key_and_keep_a_base_path(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json = [])

    real_client = httpx.AsyncClient
    monkeypatch.setattr("lydarr.jellyfin.httpx.AsyncClient",
                        lambda **kw: real_client(transport = httpx.MockTransport(handler), **kw))
    with patch("lydarr.jellyfin._request", real_request):
        assert await Jellyfin("https://example.invalid/jellyfin/", "abc123").libraries() == []

    [request] = seen
    assert str(request.url) == "https://example.invalid/jellyfin/Library/VirtualFolders"
    assert request.headers["Authorization"] == 'MediaBrowser Client="lydarr", Token="abc123"'


def test_from_config_needs_both_the_url_and_the_key(tmp_path):
    assert from_config(make_cfg(tmp_path)) is None
    assert from_config(make_cfg(tmp_path, jellyfin_url = URL)) is None
    client = from_config(make_cfg(tmp_path, jellyfin_url = URL + "/", jellyfin_api_key = KEY,
                                  jellyfin_path_map = [("/a", "/b")]))
    assert (client.url, client.api_key, client.path_map) == (URL, KEY, [("/a", "/b")])


# ── `lydarr check` ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_shows_where_each_save_path_lands_in_jellyfin(tmp_path, capsys):
    jf, _ = _server()
    cfg = make_cfg(tmp_path, jellyfin_url = URL, jellyfin_api_key = KEY,
                   jellyfin_path_map = [("/home/me/Anime", "/media/Anime")])
    with patch("lydarr.jellyfin._request", jf):
        client = await _jellyfin_check(cfg)
        lines = [await _jellyfin_line(client, path) for path in (
            "/home/me/Anime/Apothecary Diaries/Season 3", "/home/me/Anime/New Show", "/elsewhere",
        )]

    out = capsys.readouterr().out
    assert "library   Anime: /media/Anime" in out
    assert "path map  /home/me/Anime -> /media/Anime" in out
    assert lines == [
        "Anime / Apothecary Diaries: /media/Anime/Apothecary Diaries/Season 3",
        "Anime / (new show): /media/Anime/New Show",
        "no library holds /elsewhere; see JELLYFIN_PATH_MAP",
    ]
    assert jf.scans == []


@pytest.mark.asyncio
async def test_check_says_when_jellyfin_does_not_answer(tmp_path, capsys):
    jf = FakeJellyfin()
    jf.down = True
    with patch("lydarr.jellyfin._request", jf):
        assert await _jellyfin_check(make_cfg(tmp_path, jellyfin_url = URL, jellyfin_api_key = KEY)) is None
        assert await _jellyfin_check(make_cfg(tmp_path)) is None
    assert capsys.readouterr().out.startswith("! Jellyfin: cannot reach")
