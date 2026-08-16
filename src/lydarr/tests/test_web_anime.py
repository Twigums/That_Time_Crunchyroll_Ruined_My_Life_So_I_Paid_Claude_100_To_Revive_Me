import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from anilist.types import AiringEpisode, AnilistError, AnilistMedia, MediaStatus, MediaType
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry, MediaState
from lydarr.tracker import DEFAULT_CHECK_DELAY
from lydarr.web.app import create_app


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


def _make_state(entries: list[MediaEntry] | None = None) -> MediaState:
    return MediaState(entries or [])


def test_list_empty(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()
    with TestClient(create_app(cfg, state)) as client:
        resp = client.get("/api/anime/list")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_with_entries(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([
        MediaEntry(title = "Solo Leveling", media_type = "anime", submitters = ["SubsPlease"]),
    ])
    with TestClient(create_app(cfg, state)) as client:
        resp = client.get("/api/anime/list")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Solo Leveling"
    assert data[0]["submitters"] == ["SubsPlease"]
    assert data[0]["type"] == "anime"


def test_add_new_entry(tmp_path):
    from anilist.types import AnilistMedia, MediaType, MediaStatus
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    fake_media = AnilistMedia(
        id = 1,
        title_english = "Solo Leveling",
        title_romaji = None,
        media_type = MediaType.ANIME,
        status = MediaStatus.RELEASING,
        episodes = 12,
        chapters = None,
        next_airing_at = 1700000000,
        next_airing_episode = 3,
    )

    mock = AsyncMock(return_value = fake_media)
    with patch("lydarr.web.routes.anime.find_by_title", mock):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.post("/api/anime/add", json = {"title": "Solo Leveling", "type": "anime", "submitters": []})

    assert resp.status_code == 200
    data = resp.json()
    assert data["added"] is True
    assert "Solo Leveling" in state.titles()
    # Adding immediately checks AniList for that one anime's release date.
    mock.assert_awaited_once_with("Solo Leveling", MediaType.ANIME)
    assert data["status"]["status"] == "RELEASING"
    assert data["status"]["next_airing_at"] == 1700000000


def test_add_does_not_fail_on_anilist_error(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    mock = AsyncMock(side_effect = RuntimeError("anilist down"))
    with patch("lydarr.web.routes.anime.find_by_title", mock):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.post("/api/anime/add", json = {"title": "Solo Leveling"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["added"] is True
    assert data["status"] is None
    assert "Solo Leveling" in state.titles()


def test_add_while_daemon_running_spawns_tracker(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()
    app = create_app(cfg, state)

    spawn = MagicMock()
    running = MagicMock()
    running.done.return_value = False

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, return_value = None), \
         patch("lydarr.web.routes.anime.spawn_tracker", spawn):
        with TestClient(app) as client:
            app.state.daemon_task = running
            resp = client.post("/api/anime/add", json = {"title": "Solo Leveling"})

    assert resp.json()["added"] is True
    # A tracker is spawned immediately for the new entry so the daemon logs its schedule.
    spawn.assert_called_once()
    assert spawn.call_args[0][1].title == "Solo Leveling"


def test_add_while_daemon_stopped_does_not_spawn(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    spawn = MagicMock()
    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, return_value = None), \
         patch("lydarr.web.routes.anime.spawn_tracker", spawn):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.post("/api/anime/add", json = {"title": "Solo Leveling"})

    assert resp.json()["added"] is True
    spawn.assert_not_called()


def test_add_duplicate_entry(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling")])
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/add", json = {"title": "Solo Leveling"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["added"] is False
    assert data["reason"] == "already tracked"


def test_remove_existing(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling")])
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/remove", json = {"title": "Solo Leveling"})
    assert resp.status_code == 200
    assert resp.json()["removed"] is True
    assert "Solo Leveling" not in state.titles()


def test_remove_nonexistent(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/remove", json = {"title": "Nonexistent"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["removed"] is False
    assert data["reason"] == "not tracked"


def test_update_submitters(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling", submitters = [])])
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/submitters", json = {
            "title": "Solo Leveling",
            "submitters": ["SubsPlease", "Erai-raws"],
            "search_name": "Solo Leveling S2",
        })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    entries = state.entries()
    e = next(e for e in entries if e.title == "Solo Leveling")
    assert e.submitters == ["SubsPlease", "Erai-raws"]
    assert e.search_name == "Solo Leveling S2"


def _media(media_id: int, title: str) -> AnilistMedia:
    return AnilistMedia(
        id = media_id,
        title_english = title,
        title_romaji = title,
        media_type = MediaType.ANIME,
        status = MediaStatus.RELEASING,
        episodes = 12,
        chapters = None,
        next_airing_at = None,
        next_airing_episode = None,
    )


def _calendar_state() -> MediaState:
    return _make_state([
        MediaEntry(title = "Solo Leveling"),
        MediaEntry(title = "Dan Da Dan"),
        MediaEntry(title = "One Piece", media_type = "manga"),
        MediaEntry(title = "Old Show", deprecated = True),
    ])


def test_calendar_returns_episodes_for_tracked_anime(tmp_path):
    cfg = _make_cfg(tmp_path)
    looked_up = []

    async def fake_find(title, media_type):
        looked_up.append(title)
        return {"Solo Leveling": _media(1, "Solo Leveling"),
                "Dan Da Dan": _media(2, "Dan Da Dan")}.get(title)

    schedules = [
        AiringEpisode(media_id = 1, episode = 5, airing_at = 1755000000, duration = 24),
        AiringEpisode(media_id = 2, episode = 9, airing_at = 1755100000, duration = 23),
        AiringEpisode(media_id = 77, episode = 1, airing_at = 1755200000, duration = 24),
    ]
    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, side_effect = fake_find), \
         patch("lydarr.web.routes.anime.airing_schedules", new_callable = AsyncMock, return_value = schedules) as mock_sched, \
         TestClient(create_app(cfg, _calendar_state())) as client:
        resp = client.get("/api/anime/calendar?start=1754000000&end=1756000000")

    assert resp.status_code == 200
    data = resp.json()
    assert data["unresolved"] == []
    # Manga and deprecated entries are not on the calendar.
    assert sorted(looked_up) == ["Dan Da Dan", "Solo Leveling"]
    assert mock_sched.call_args.args == ([1, 2], 1754000000, 1756000000)
    # An id we did not ask about is dropped rather than shown without a title.
    assert data["episodes"] == [
        {"media_id": 1, "episode": 5, "airing_at": 1755000000, "duration": 24, "title": "Solo Leveling"},
        {"media_id": 2, "episode": 9, "airing_at": 1755100000, "duration": 23, "title": "Dan Da Dan"},
    ]


def test_calendar_reports_titles_missing_from_anilist(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling"), MediaEntry(title = "Nonexistent Show")])

    async def fake_find(title, media_type):
        return _media(1, title) if title == "Solo Leveling" else None

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, side_effect = fake_find), \
         patch("lydarr.web.routes.anime.airing_schedules", new_callable = AsyncMock, return_value = []), \
         TestClient(create_app(cfg, state)) as client:
        data = client.get("/api/anime/calendar?start=1&end=2").json()

    assert data["unresolved"] == ["Nonexistent Show"]
    assert data["episodes"] == []


def test_calendar_survives_anilist_failure(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling")])

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock,
               return_value = _media(1, "Solo Leveling")), \
         patch("lydarr.web.routes.anime.airing_schedules", new_callable = AsyncMock,
               side_effect = AnilistError("Too Many Requests")), \
         TestClient(create_app(cfg, state)) as client:
        resp = client.get("/api/anime/calendar?start=1&end=2")

    assert resp.status_code == 200
    data = resp.json()
    assert data["episodes"] == []
    assert "Too Many Requests" in data["error"]


def test_calendar_caches_repeated_ranges(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling")])

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock,
               return_value = _media(1, "Solo Leveling")), \
         patch("lydarr.web.routes.anime.airing_schedules", new_callable = AsyncMock,
               return_value = []) as mock_sched, \
         TestClient(create_app(cfg, state)) as client:
        client.get("/api/anime/calendar?start=1&end=2")
        client.get("/api/anime/calendar?start=1&end=2")
        client.get("/api/anime/calendar?start=1&end=3")

    assert mock_sched.await_count == 2


def test_calendar_rejects_empty_range(tmp_path):
    cfg = _make_cfg(tmp_path)
    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock) as mock_find, \
         TestClient(create_app(cfg, _calendar_state())) as client:
        data = client.get("/api/anime/calendar?start=100&end=100").json()

    assert data == {"episodes": [], "unresolved": []}
    mock_find.assert_not_called()


def test_update_check_delay(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state([MediaEntry(title = "Solo Leveling", submitters = [])])
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/submitters", json = {
            "title": "Solo Leveling",
            "submitters": [],
            "check_delay": 15,
        })
        assert resp.json()["ok"] is True
        assert state.get("Solo Leveling").check_delay == 15

        listed = client.get("/api/anime/list").json()[0]
        assert listed["check_delay"] == 15
        assert listed["default_check_delay"] == DEFAULT_CHECK_DELAY

        client.post("/api/anime/submitters", json = {
            "title": "Solo Leveling",
            "submitters": [],
            "check_delay": -5,
        })
        assert state.get("Solo Leveling").check_delay == 0


def test_update_submitters_not_tracked(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()
    with TestClient(create_app(cfg, state)) as client:
        resp = client.post("/api/anime/submitters", json = {
            "title": "Nonexistent",
            "submitters": [],
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["reason"] == "not tracked"


def test_search_anime(tmp_path):
    from anilist.types import AnilistMedia, MediaType, MediaStatus
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    fake_media = AnilistMedia(
        id = 153406,
        title_english = "Solo Leveling Season 2",
        title_romaji = "Solo Leveling Season 2 -Arise from the Shadow-",
        media_type = MediaType.ANIME,
        status = MediaStatus.FINISHED,
        episodes = 25,
        chapters = None,
        next_airing_at = None,
        next_airing_episode = None,
    )

    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock, return_value = [fake_media]):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/anime/search?q=Solo+Leveling")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == 153406
    assert data[0]["episodes"] == 25
    assert data[0]["status"] == "FINISHED"


def test_search_manga_type(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    with patch("lydarr.web.routes.anime.anilist_search", new_callable = AsyncMock, return_value = []) as mock_search:
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/anime/search?q=One+Piece&type=manga")

    assert resp.status_code == 200
    from anilist.types import MediaType
    mock_search.assert_called_once_with("One Piece", MediaType.MANGA)


def test_get_status_found(tmp_path):
    from anilist.types import AnilistMedia, MediaType, MediaStatus
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    fake_media = AnilistMedia(
        id = 153406,
        title_english = "Solo Leveling Season 2",
        title_romaji = None,
        media_type = MediaType.ANIME,
        status = MediaStatus.FINISHED,
        episodes = 25,
        chapters = None,
        next_airing_at = None,
        next_airing_episode = None,
    )

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, return_value = fake_media):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/anime/status?title=Solo+Leveling")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FINISHED"


def test_get_status_not_found(tmp_path):
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    with patch("lydarr.web.routes.anime.find_by_title", new_callable = AsyncMock, return_value = None):
        with TestClient(create_app(cfg, state)) as client:
            resp = client.get("/api/anime/status?title=Nonexistent")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] is None


def test_get_status_is_cached(tmp_path):
    from anilist.types import AnilistMedia, MediaType, MediaStatus
    cfg = _make_cfg(tmp_path)
    state = _make_state()

    fake_media = AnilistMedia(
        id = 153406,
        title_english = "Solo Leveling Season 2",
        title_romaji = None,
        media_type = MediaType.ANIME,
        status = MediaStatus.RELEASING,
        episodes = 25,
        chapters = None,
        next_airing_at = 1700000000,
        next_airing_episode = 5,
    )

    mock = AsyncMock(return_value = fake_media)
    with patch("lydarr.web.routes.anime.find_by_title", mock):
        with TestClient(create_app(cfg, state)) as client:
            first = client.get("/api/anime/status?title=Solo+Leveling")
            second = client.get("/api/anime/status?title=Solo+Leveling")
            third = client.get("/api/anime/status?title=Solo+Leveling")

    assert first.json() == second.json() == third.json()
    assert first.json()["next_airing_episode"] == 5
    # Despite three page-load requests, AniList is hit only once.
    assert mock.await_count == 1
