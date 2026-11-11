import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from anilist.types import AnilistRateLimited, AnilistUnavailable, MediaStatus, MediaType
from lydarr.file_manager import MediaEntry, read_entries
from lydarr.tests.webrig import WebRig, make_media
from lydarr.tracker import DEFAULT_CHECK_DELAY

NOW = int(time.time())


# ── list ─────────────────────────────────────────────────────────────────────

def test_list_empty(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        data = client.get("/api/anime/list").json()

    assert data["entries"] == []
    assert data["default_dir"] == rig.cfg.default_dir
    assert data["anilist"]["error"] is None


def test_list_carries_status_inline_without_any_lookup(tmp_path):
    entries = [
        MediaEntry(title = "Solo Leveling", anilist_id = 1, submitters = ["SubsPlease"]),
        MediaEntry(title = "One Piece", media_type = "manga", anilist_id = 2, last_chapter = 1100),
    ]
    media = [
        make_media(1, "Solo Leveling", cover_image = "https://img/1.jpg"),
        make_media(2, "One Piece", media_type = MediaType.MANGA),
    ]
    rig = WebRig(tmp_path, entries, media = media, schedule = {1: {7: NOW + 3600, 6: NOW - 3600}})

    with patch("anilist.search.graphql", new_callable = AsyncMock) as graphql:
        with TestClient(rig.app) as client:
            data = client.get("/api/anime/list").json()
        graphql.assert_not_awaited()

    anime, manga = data["entries"]
    assert anime["title"] == "Solo Leveling"
    assert anime["status"]["status"] == "RELEASING"
    assert (anime["status"]["next_airing_episode"], anime["status"]["next_airing_at"]) == (7, NOW + 3600)
    assert anime["status"]["cover_image"] == "https://img/1.jpg"
    assert anime["default_check_delay"] == DEFAULT_CHECK_DELAY
    assert anime["progress"]["last"] is None
    assert manga["type"] == "manga"
    assert manga["last_chapter"] == 1100
    assert manga["status"]["next_airing_at"] is None
    assert rig.refreshes == [False]


def test_list_shows_the_same_default_folder_the_tracker_uses(tmp_path):
    entry = MediaEntry(title = "FX Senshi Kurumi-chan", anilist_id = 5)
    rig = WebRig(tmp_path, [entry], media = [make_media(5, "FX Fighter Kurumi-chan", "FX Senshi Kurumi-chan")])
    with TestClient(rig.app) as client:
        [row] = client.get("/api/anime/list").json()["entries"]

    expected = str(tmp_path / "library" / "FX Fighter Kurumi-chan")
    assert row["default_path"] == expected
    assert row["path"] == expected
    assert row["download_dir"] == ""


def test_list_reports_an_entrys_own_path(tmp_path):
    entry = MediaEntry(title = "Show", anilist_id = 5, download_dir = "/media/Anime/Show/Season 2")
    rig = WebRig(tmp_path, [entry], media = [make_media(5, "Show")])
    with TestClient(rig.app) as client:
        [row] = client.get("/api/anime/list").json()["entries"]
    assert row["path"] == "/media/Anime/Show/Season 2"


def test_list_marks_unresolved_entries(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Ghost")])
    rig.store._unresolved["Ghost"] = time.time()
    with TestClient(rig.app) as client:
        data = client.get("/api/anime/list").json()
    assert data["entries"][0]["status"]["unresolved"] is True
    assert data["anilist"]["unresolved"] == ["Ghost"]


def test_refresh_endpoint_forces_a_refresh(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        resp = client.post("/api/anime/refresh").json()
    assert resp["ok"] is True
    assert rig.refreshes == [True]


# ── add / remove / archive ───────────────────────────────────────────────────

def test_add_stores_the_anilist_id_and_starts_tracking_from_now(tmp_path):
    rig = WebRig(tmp_path)
    found = make_media(151807, "Solo Leveling", cover_image = "https://img/sl.jpg")

    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock, return_value = [found]):
        with TestClient(rig.app) as client:
            client.get("/api/anime/search?q=solo")
            resp = client.post("/api/anime/add", json = {
                "title": "Solo Leveling", "type": "anime", "anilist_id": 151807,
            }).json()

    assert resp["added"] is True
    assert resp["entry"]["anilist_id"] == 151807
    assert resp["entry"]["status"]["cover_image"] == "https://img/sl.jpg"
    assert read_entries(rig.cfg.anime_file)[0].anilist_id == 151807
    show = rig.tstate.peek("Solo Leveling")
    assert show is not None and show.backfill is False
    assert abs(show.since - time.time()) < 5


def test_add_without_an_id_still_works(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        resp = client.post("/api/anime/add", json = {"title": "One Piece", "type": "manga"}).json()
    assert resp["added"] is True
    assert rig.state.get("One Piece").media_type == "manga"
    assert rig.state.get("One Piece").anilist_id == 0


def test_add_with_backfill_queues_earlier_episodes(tmp_path):
    rig = WebRig(tmp_path)
    with patch.object(rig.store, "fetch_full_schedule", new_callable = AsyncMock) as fetch:
        with TestClient(rig.app) as client:
            resp = client.post("/api/anime/add", json = {
                "title": "Finished Show", "anilist_id": 9, "backfill": True,
            }).json()
    assert resp["added"] is True and resp["warning"] is None
    fetch.assert_awaited_once()
    assert rig.tstate.peek("Finished Show").backfill is True


def test_add_with_backfill_survives_an_anilist_outage(tmp_path):
    rig = WebRig(tmp_path)
    with patch.object(rig.store, "fetch_full_schedule", new_callable = AsyncMock,
                      side_effect = AnilistUnavailable("down")):
        with TestClient(rig.app) as client:
            resp = client.post("/api/anime/add", json = {"title": "Show", "anilist_id": 9, "backfill": True}).json()
    assert resp["added"] is True
    assert "down" in resp["warning"]


def test_add_duplicate_entry(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Solo Leveling")])
    with TestClient(rig.app) as client:
        data = client.post("/api/anime/add", json = {"title": "Solo Leveling"}).json()
    assert data == {"added": False, "reason": "already tracked"}


def test_remove_forgets_the_entrys_history(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Solo Leveling")])
    rig.tstate.show("Solo Leveling")
    with TestClient(rig.app) as client:
        assert client.post("/api/anime/remove", json = {"title": "Solo Leveling"}).json() == {"removed": True}
        missing = client.post("/api/anime/remove", json = {"title": "Solo Leveling"}).json()
    assert rig.state.titles() == []
    assert rig.tstate.peek("Solo Leveling") is None
    assert missing == {"removed": False, "reason": "not tracked"}


def test_archive_and_restore(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Show")])
    old = rig.tstate.show("Show", now = 1000.0)
    old.completed_at = 2000.0

    with TestClient(rig.app) as client:
        assert client.post("/api/anime/deprecate", json = {"title": "Show"}).json() == {"ok": True}
        assert rig.state.get("Show").deprecated is True
        assert client.post("/api/anime/reactivate", json = {"title": "Show"}).json() == {"ok": True}
        assert client.post("/api/anime/deprecate", json = {"title": "Nope"}).json()["ok"] is False
        assert client.post("/api/anime/reactivate", json = {"title": "Nope"}).json()["ok"] is False

    assert rig.state.get("Show").deprecated is False
    show = rig.tstate.peek("Show")
    assert show.completed_at == 0.0
    assert abs(show.since - time.time()) < 5


# ── update ───────────────────────────────────────────────────────────────────

def test_update_saves_every_editable_field(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Show", anilist_id = 3)], media = [make_media(3, "Show")])
    body = {
        "title": "Show", "submitters": ["SubsPlease", "Erai-raws"], "search_name": "  Shou  ",
        "download_dir": " /media/Anime/Show/Season 2 ", "check_delay": 145, "episode_offset": 48,
    }
    with TestClient(rig.app) as client:
        resp = client.post("/api/anime/update", json = body).json()

    entry = rig.state.get("Show")
    assert resp["ok"] is True
    assert resp["entry"]["path"] == "/media/Anime/Show/Season 2"
    assert entry.submitters == ["SubsPlease", "Erai-raws"]
    assert entry.search_name == "Shou"
    assert entry.download_dir == "/media/Anime/Show/Season 2"
    assert entry.check_delay == 145
    assert entry.episode_offset == 48
    assert entry.anilist_id == 3


def test_update_keeps_the_old_route_and_clamps_negative_delay(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Show", check_delay = 90)])
    with TestClient(rig.app) as client:
        resp = client.post("/api/anime/submitters", json = {"title": "Show", "submitters": [], "check_delay": -5})
    assert resp.json()["ok"] is True
    assert rig.state.get("Show").check_delay == 0


def test_update_not_tracked(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        data = client.post("/api/anime/update", json = {"title": "Ghost", "submitters": []}).json()
    assert data == {"ok": False, "reason": "not tracked"}


def test_backfill_route(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Show", anilist_id = 3)])
    with patch.object(rig.store, "fetch_full_schedule", new_callable = AsyncMock):
        with TestClient(rig.app) as client:
            assert client.post("/api/anime/backfill", json = {"title": "Show"}).json() == {"ok": True, "error": None}
            assert client.post("/api/anime/backfill", json = {"title": "Nope"}).json()["ok"] is False


# ── search ───────────────────────────────────────────────────────────────────

def test_search_marks_tracked_shows_and_reuses_recent_results(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Solo Leveling", anilist_id = 1)])
    results = [
        make_media(1, "Solo Leveling", status = MediaStatus.FINISHED, format = "TV"),
        make_media(2, "Solo Leveling Season 2"),
    ]
    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock, return_value = results) as search:
        with TestClient(rig.app) as client:
            first = client.get("/api/anime/search?q=Solo Leveling").json()
            again = client.get("/api/anime/search?q=solo leveling ").json()

    assert search.await_count == 1
    assert search.await_args.kwargs == {"interactive": True}
    assert first == again
    assert [(r["id"], r["tracked"]) for r in first] == [(1, True), (2, False)]
    assert first[0]["format"] == "TV"
    assert first[0]["type"] == "anime"


def test_search_manga_type(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock, return_value = []) as search:
        with TestClient(rig.app) as client:
            resp = client.get("/api/anime/search?q=One+Piece&type=manga")
    assert resp.json() == []
    assert search.await_args.args[1] == MediaType.MANGA


def test_search_failure_is_a_clean_503_with_retry_hint(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock,
               side_effect = AnilistRateLimited("AniList rate limit reached; retry in 40s", 40)):
        with TestClient(rig.app) as client:
            resp = client.get("/api/anime/search?q=x")
    assert resp.status_code == 503
    assert resp.json() == {"error": "AniList: AniList rate limit reached; retry in 40s", "retry_after": 40}


# ── calendar ─────────────────────────────────────────────────────────────────

def _calendar_rig(tmp_path) -> WebRig:
    entries = [
        MediaEntry(title = "One Piece", anilist_id = 21),
        MediaEntry(title = "Frieren", anilist_id = 99),
        MediaEntry(title = "Berserk", media_type = "manga", anilist_id = 30),
        MediaEntry(title = "Old Show", anilist_id = 40, deprecated = True),
    ]
    media = [make_media(21, "One Piece", duration = 24), make_media(99, "Frieren")]
    schedule = {21: {1174: 1500}, 99: {3: 1800}, 40: {9: 1600}}
    rig = WebRig(tmp_path, entries, media = media, schedule = schedule)
    rig.store._window = (0, 10_000)
    rig.store._scheduled_ids = {21, 99}
    return rig


def test_calendar_is_served_from_the_cache(tmp_path):
    rig = _calendar_rig(tmp_path)
    with patch("lydarr.media_store.airing_schedules", new_callable = AsyncMock) as fetch:
        with TestClient(rig.app) as client:
            data = client.get("/api/anime/calendar?start=1000&end=2000").json()
            client.get("/api/anime/calendar?start=1000&end=2000")
        fetch.assert_not_awaited()

    assert data["episodes"] == [
        {"media_id": 21, "episode": 1174, "airing_at": 1500, "duration": 24, "title": "One Piece"},
        {"media_id": 99, "episode": 3, "airing_at": 1800, "duration": None, "title": "Frieren"},
    ]
    assert data["unresolved"] == []
    assert "error" not in data


def test_calendar_fetches_only_what_lies_outside_the_cached_window(tmp_path):
    rig = _calendar_rig(tmp_path)
    with patch("lydarr.media_store.airing_schedules", new_callable = AsyncMock, return_value = []) as fetch:
        with TestClient(rig.app) as client:
            client.get("/api/anime/calendar?start=9000&end=12000")
    fetch.assert_awaited_once_with([21, 99], 10_000, 12_000)


def test_calendar_keeps_showing_cached_episodes_when_anilist_is_down(tmp_path):
    rig = _calendar_rig(tmp_path)
    with patch("lydarr.media_store.airing_schedules", new_callable = AsyncMock,
               side_effect = AnilistUnavailable("HTTP 500: boom")):
        with TestClient(rig.app) as client:
            resp = client.get("/api/anime/calendar?start=1000&end=20000")

    assert resp.status_code == 200
    data = resp.json()
    assert data["error"] == "HTTP 500: boom"
    assert len(data["episodes"]) == 2


def test_calendar_rejects_empty_range(tmp_path):
    rig = _calendar_rig(tmp_path)
    with TestClient(rig.app) as client:
        data = client.get("/api/anime/calendar?start=200&end=100").json()
    assert data == {"episodes": [], "unresolved": []}
