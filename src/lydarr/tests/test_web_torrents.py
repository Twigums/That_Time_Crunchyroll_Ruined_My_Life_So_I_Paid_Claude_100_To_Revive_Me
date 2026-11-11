from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from lydarr.file_manager import MediaEntry
from lydarr.tests.webrig import WebRig, make_media, make_torrent
from lydarr.torrent_client import AddedTorrent
from nyaa.types import HttpError, TorrentSite

AIRED = 1_800_000_000
SUBS = "[SubsPlease] Solo Leveling - 25 (1080p) [0047E2A3].mkv"
HEVC = "[Judas] Solo Leveling - S01E25 [1080p][HEVC x265 10bit]"


def _added(hash_: str = "f" * 40) -> AddedTorrent:
    return AddedTorrent(id = 1, hash = hash_, name = "x")


# ── search ───────────────────────────────────────────────────────────────────

def test_search_returns_parsed_release_details(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock,
               return_value = [make_torrent(SUBS), make_torrent(HEVC, number = 2)]) as search:
        with TestClient(rig.app) as client:
            data = client.get("/api/torrents/search?q=Solo+Leveling").json()

    assert data["judged"] is False
    first, second = data["results"]
    assert first["name"] == SUBS
    assert first["magnet"].startswith("magnet:?xt=urn:btih:")
    assert first["download_url"] == "https://nyaa.si/download/1.torrent"
    assert (first["group"], first["episode"], first["resolution"], first["hevc"]) == ("SubsPlease", 25, 1080, False)
    assert (second["group"], second["season"], second["hevc"]) == ("Judas", 1, True)
    assert first["verdict"] is None
    params = search.await_args.args[1]
    assert (params.keyword, params.category_param) == ("Solo Leveling", "1_2")


def test_search_with_episode_number_only_narrows_the_query(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = []) as search:
        with TestClient(rig.app) as client:
            client.get("/api/torrents/search?q=Solo+Leveling&ep=5")
    assert search.await_args.args[1].keyword == "Solo Leveling 05"


def test_search_bound_to_an_entry_and_episode_shows_the_trackers_verdicts(tmp_path):
    entry = MediaEntry(title = "Solo Leveling", anilist_id = 1, search_name = "Solo Leveling")
    rig = WebRig(tmp_path, [entry], media = [make_media(1, "Solo Leveling")],
                 schedule = {1: {25: AIRED, 26: AIRED + 7 * 86400}})
    torrents = [
        make_torrent(HEVC, AIRED + 3600, number = 1),
        make_torrent("[Judas] Solo Leveling - S01E12 [1080p][HEVC x265 10bit]", AIRED - 90 * 86400, number = 2),
    ]
    with patch("lydarr.tracker.find_candidates", new_callable = AsyncMock, return_value = torrents) as find:
        with TestClient(rig.app) as client:
            data = client.get("/api/torrents/search?q=Solo+Leveling&ep=25&entry=Solo+Leveling").json()

    assert find.await_args.args[0] == "Solo Leveling"
    assert data["judged"] is True
    assert [(r["verdict"], r["reason"]) for r in data["results"]] == [
        ("match", "matches"),
        ("rejected", "uploaded before this episode aired"),
    ]


def test_search_for_a_manga_entry_uses_the_literature_category(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Berserk", media_type = "manga")])
    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = []) as search:
        with TestClient(rig.app) as client:
            client.get("/api/torrents/search?q=Berserk&entry=Berserk")
            client.get("/api/torrents/search?q=Berserk&kind=manga")
    assert [c.args[1].category_param for c in search.await_args_list] == ["3_1", "3_1"]


def test_search_unknown_site_defaults_to_nyaa_and_sukebei_searches_everything(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock, return_value = []) as search:
        with TestClient(rig.app) as client:
            client.get("/api/torrents/search?q=x&site=bogus")
            client.get("/api/torrents/search?q=x&site=sukebei")
    assert search.await_args_list[0].args[0] == TorrentSite.NYAA_SI
    assert search.await_args_list[1].args[0] == TorrentSite.SUKEBEI_NYAA_SI
    assert search.await_args_list[1].args[1].category_param == "0_0"


def test_search_failure_is_a_clean_502(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.torrents.nyaa_search", new_callable = AsyncMock,
               side_effect = HttpError("Temporary failure in name resolution")):
        with TestClient(rig.app) as client:
            resp = client.get("/api/torrents/search?q=x")
    assert resp.status_code == 502
    assert resp.json() == {"error": "Nyaa: Temporary failure in name resolution"}


# ── add ──────────────────────────────────────────────────────────────────────

def test_add_bound_to_an_entry_saves_to_that_entrys_path(tmp_path):
    target = str(tmp_path / "Jellyfin" / "Solo Leveling" / "Season 1")
    entry = MediaEntry(title = "Solo Leveling", anilist_id = 1, download_dir = target)
    rig = WebRig(tmp_path, [entry], media = [make_media(1, "Solo Leveling")], schedule = {1: {25: AIRED}})
    torrent = make_torrent(SUBS, AIRED + 3600)

    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock, return_value = _added()) as add:
        with TestClient(rig.app) as client:
            data = client.post("/api/torrents/add", json = {
                "magnet": torrent.magnet, "name": torrent.name, "entry": "Solo Leveling", "episode": 25,
                "published": torrent.published,
            }).json()

    assert data == {"ok": True, "error": None, "dir": target, "episode": 25}
    assert add.await_args.args[4] == target
    assert rig.tstate.peek("Solo Leveling").episodes[25].status == "grabbed"
    assert rig.tstate.activity()[0]["kind"] == "manual"


def test_add_without_an_entry_files_under_the_releases_own_title(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock, return_value = _added()) as add:
        with TestClient(rig.app) as client:
            data = client.post("/api/torrents/add", json = {
                "magnet": "magnet:?xt=urn:btih:" + "a" * 40, "name": "[SubsPlease] Konosuba - 03 (1080p)",
            }).json()

    assert data["ok"] is True
    assert data["dir"] == str(tmp_path / "library" / "Konosuba")
    assert add.await_args.args[4] == data["dir"]


def test_add_falls_back_to_the_magnets_display_name(tmp_path):
    rig = WebRig(tmp_path)
    magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=%5BSubsPlease%5D%20Konosuba%20-%2003%20%281080p%29"
    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock, return_value = _added()):
        with TestClient(rig.app) as client:
            data = client.post("/api/torrents/add", json = {"magnet": magnet}).json()
    assert data["dir"] == str(tmp_path / "library" / "Konosuba")


def test_add_for_an_entry_that_was_removed_is_refused(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock) as add:
        with TestClient(rig.app) as client:
            data = client.post("/api/torrents/add", json = {
                "magnet": "magnet:?xt=urn:btih:" + "a" * 40, "entry": "Gone",
            }).json()
    assert data == {"ok": False, "error": "Gone is no longer in the watchlist"}
    add.assert_not_awaited()


def test_add_reports_transmission_failure(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock, return_value = None):
        with TestClient(rig.app) as client:
            data = client.post("/api/torrents/add", json = {"magnet": "magnet:?xt=urn:btih:" + "a" * 40}).json()
    assert data["ok"] is False
    assert "Transmission" in data["error"]


def test_add_rejects_non_magnet_links(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        resp = client.post("/api/torrents/add", json = {"magnet": "https://example.com/file.torrent"})
    assert resp.status_code == 422


# ── activity ─────────────────────────────────────────────────────────────────

def test_activity_lists_additions_and_review_holds(tmp_path):
    entry = MediaEntry(title = "Solo Leveling", anilist_id = 1)
    rig = WebRig(tmp_path, [entry], media = [make_media(1, "Solo Leveling")], schedule = {1: {25: AIRED}})
    rec = rig.tstate.episode("Solo Leveling", 25)
    rec.status = "review"
    rec.note = "held"
    rec.candidates = [{"hash": "abc", "name": SUBS, "magnet": "magnet:?xt=urn:btih:abc", "group": "SubsPlease",
                       "episode": 37, "published": AIRED + 3600}]
    rig.tstate.log("grabbed", "Solo Leveling", 24, name = "previous", directory = "/x")

    with TestClient(rig.app) as client:
        data = client.get("/api/activity").json()

    assert [e["kind"] for e in data["events"]] == ["grabbed"]
    [held] = data["review"]
    assert (held["title"], held["episode"]) == ("Solo Leveling", 25)
    assert held["candidates"][0]["hash"] == "abc"
    assert "magnet" not in held["candidates"][0]


def test_review_can_be_approved_skipped_and_retried(tmp_path):
    entry = MediaEntry(title = "Solo Leveling", anilist_id = 1)
    rig = WebRig(tmp_path, [entry], media = [make_media(1, "Solo Leveling")],
                 schedule = {1: {25: AIRED, 26: AIRED + 7 * 86400}})
    for episode in (25, 26):
        rec = rig.tstate.episode("Solo Leveling", episode)
        rec.status = "review"
        rec.candidates = [{"hash": "abc", "name": SUBS, "magnet": "magnet:?xt=urn:btih:" + "c" * 40,
                           "group": "SubsPlease", "published": AIRED + 3600}]

    with patch("lydarr.tracker.add_magnet", new_callable = AsyncMock, return_value = _added()) as add:
        with TestClient(rig.app) as client:
            approved = client.post("/api/activity/approve",
                                   json = {"title": "Solo Leveling", "episode": 25, "hash": "abc"}).json()
            stale = client.post("/api/activity/approve",
                                json = {"title": "Solo Leveling", "episode": 26, "hash": "zzz"}).json()
            skipped = client.post("/api/activity/skip", json = {"title": "Solo Leveling", "episode": 26}).json()
            retried = client.post("/api/activity/retry", json = {"title": "Solo Leveling", "episode": 26}).json()

    assert approved == {"ok": True, "error": None}
    assert add.await_count == 1
    assert stale["ok"] is False
    assert skipped["ok"] is True and retried["ok"] is True
    episodes = rig.tstate.peek("Solo Leveling").episodes
    assert episodes[25].status == "grabbed"
    assert episodes[26].status == "pending"
