import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta

from anilist.types import AnilistMedia, MediaType, MediaStatus
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry, MediaState
from lydarr.tracker import DEFAULT_CHECK_DELAY, _log, _pad, _step_anime, track_media


def _make_cfg() -> AppConfig:
    return AppConfig(
        anime_file = "/tmp/test.toml",
        transmission_url = "http://localhost:9091/transmission/rpc",
        transmission_user = None,
        transmission_pass = None,
        default_dir = "/downloads",
        default_dir_set = True,
        lydarr_user = None,
        lydarr_pass = None,
    )


def _make_state(titles: list[str] | None = None) -> MediaState:
    entries = [MediaEntry(title = t) for t in (titles or [])]
    return MediaState(entries)


def _make_info(
    status: MediaStatus,
    episodes: int | None = 25,
    next_airing_at: int | None = None,
    next_airing_episode: int | None = None,
) -> AnilistMedia:
    return AnilistMedia(
        id = 1,
        title_english = "Solo Leveling",
        title_romaji = "Solo Leveling",
        media_type = MediaType.ANIME,
        status = status,
        episodes = episodes,
        chapters = None,
        next_airing_at = next_airing_at,
        next_airing_episode = next_airing_episode,
    )


def test_log_prints(capsys):
    _log("Solo Leveling", "Test message")
    captured = capsys.readouterr()
    assert "[Solo Leveling] Test message" in captured.out


def test_pad():
    assert _pad(1) == "01"
    assert _pad(10) == "10"
    assert _pad(25) == "25"


@pytest.mark.asyncio
async def test_step_anime_not_found_retries():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling")

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = None), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
        result = await _step_anime(cfg, state, entry)

    mock_sleep.assert_called_once_with(6 * 3600)
    assert result is True


@pytest.mark.asyncio
async def test_step_anime_finished_downloads_all():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling", submitters = ["SubsPlease"])
    info = _make_info(MediaStatus.FINISHED, episodes = 25)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker._download_all_episodes", new_callable = AsyncMock) as mock_dl, \
         patch.object(state, "remove", new_callable = AsyncMock) as mock_remove:
        result = await _step_anime(cfg, state, entry)

    mock_dl.assert_called_once_with(cfg, "Solo Leveling", 25, ["SubsPlease"], "/downloads/Solo Leveling")
    mock_remove.assert_called_once_with("/tmp/test.toml", "Solo Leveling")
    assert result is False


@pytest.mark.asyncio
async def test_step_anime_finished_unknown_episode_count():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling")
    info = _make_info(MediaStatus.FINISHED, episodes = None)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
        result = await _step_anime(cfg, state, entry)

    mock_sleep.assert_called_once_with(6 * 3600)
    assert result is True


@pytest.mark.asyncio
async def test_step_anime_search_name_used_for_nyaa_only():
    cfg = _make_cfg()
    state = _make_state(["That Time I Got Reincarnated as a Slime Season 4"])
    entry = MediaEntry(
        title = "That Time I Got Reincarnated as a Slime Season 4",
        search_name = "Tensei Shitara Slime Datta Ken 4th Season",
        submitters = [],
    )
    info = _make_info(MediaStatus.FINISHED, episodes = 2)

    lookup_calls = []

    async def mock_find(name, media_type):
        lookup_calls.append(name)
        return info

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, side_effect = mock_find), \
         patch("lydarr.tracker._download_all_episodes", new_callable = AsyncMock) as mock_dl, \
         patch.object(state, "remove", new_callable = AsyncMock):
        await _step_anime(cfg, state, entry)

    assert lookup_calls == ["That Time I Got Reincarnated as a Slime Season 4"]
    mock_dl.assert_called_once_with(cfg, "Tensei Shitara Slime Datta Ken 4th Season", 2, [], "/downloads/Solo Leveling")


@pytest.mark.asyncio
async def test_step_anime_releasing_with_next_episode():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling", submitters = [])
    future_ts = int((datetime.now(tz = timezone.utc) + timedelta(hours = 2)).timestamp())
    info = _make_info(MediaStatus.RELEASING, next_airing_at = future_ts, next_airing_episode = 5)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker._sleep_until", new_callable = AsyncMock), \
         patch("lydarr.tracker._wait_and_add_episode", new_callable = AsyncMock), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock):
        result = await _step_anime(cfg, state, entry)

    assert result is True


@pytest.mark.asyncio
@pytest.mark.parametrize("check_delay, expected_minutes", [(0, DEFAULT_CHECK_DELAY), (15, 15), (240, 240)])
async def test_step_anime_releasing_honours_check_delay(check_delay, expected_minutes):
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling", submitters = [], check_delay = check_delay)
    air_time = datetime.now(tz = timezone.utc) + timedelta(hours = 2)
    info = _make_info(MediaStatus.RELEASING, next_airing_at = int(air_time.timestamp()),
                      next_airing_episode = 5)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker._sleep_until", new_callable = AsyncMock) as mock_sleep_until, \
         patch("lydarr.tracker._wait_and_add_episode", new_callable = AsyncMock):
        await _step_anime(cfg, state, entry)

    trigger = mock_sleep_until.call_args.args[0]
    aired_at = datetime.fromtimestamp(int(air_time.timestamp()), tz = timezone.utc)
    assert (trigger - aired_at) == timedelta(minutes = expected_minutes)


@pytest.mark.asyncio
async def test_step_anime_releasing_no_schedule():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling", submitters = [])
    info = _make_info(MediaStatus.RELEASING, next_airing_at = None, next_airing_episode = None)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
        result = await _step_anime(cfg, state, entry)

    mock_sleep.assert_called_once_with(6 * 3600)
    assert result is True


@pytest.mark.asyncio
async def test_step_anime_not_yet_released():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling")
    info = _make_info(MediaStatus.NOT_YET_RELEASED)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
        result = await _step_anime(cfg, state, entry)

    mock_sleep.assert_called_once_with(6 * 3600)
    assert result is True


@pytest.mark.asyncio
async def test_step_anime_cancelled():
    cfg = _make_cfg()
    state = _make_state(["Solo Leveling"])
    entry = MediaEntry(title = "Solo Leveling")
    info = _make_info(MediaStatus.CANCELLED)

    with patch("lydarr.tracker.find_by_title", new_callable = AsyncMock, return_value = info), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock) as mock_sleep:
        result = await _step_anime(cfg, state, entry)

    mock_sleep.assert_called_once_with(3600)
    assert result is True


@pytest.mark.asyncio
async def test_track_media_stops_on_false():
    cfg = _make_cfg()
    state = _make_state()
    entry = MediaEntry(title = "Solo Leveling")

    call_count = 0

    async def mock_step(_, s, e):
        nonlocal call_count
        call_count += 1
        return False

    with patch("lydarr.tracker._step_anime", new_callable = AsyncMock, side_effect = mock_step):
        await track_media(cfg, state, entry)

    assert call_count == 1


@pytest.mark.asyncio
async def test_track_media_retries_on_exception():
    cfg = _make_cfg()
    state = _make_state()
    entry = MediaEntry(title = "Solo Leveling")

    call_count = 0

    async def mock_step(_, s, e):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Unexpected error")
        return False

    with patch("lydarr.tracker._step_anime", new_callable = AsyncMock, side_effect = mock_step), \
         patch("lydarr.tracker.asyncio.sleep", new_callable = AsyncMock):
        await track_media(cfg, state, entry)

    assert call_count == 2


@pytest.mark.asyncio
async def test_track_media_manga_uses_step_manga():
    cfg = _make_cfg()
    state = _make_state()
    entry = MediaEntry(title = "One Piece", media_type = "manga")

    with patch("lydarr.tracker._step_manga", new_callable = AsyncMock, return_value = False) as mock_manga, \
         patch("lydarr.tracker._step_anime", new_callable = AsyncMock, return_value = False) as mock_anime:
        await track_media(cfg, state, entry)

    mock_manga.assert_called_once()
    mock_anime.assert_not_called()


@pytest.mark.asyncio
async def test_track_media_picks_up_updated_entry():
    """search_name updates written to state are reflected on subsequent iterations."""
    entry = MediaEntry(title = "Solo Leveling")
    state = MediaState([entry])
    cfg = _make_cfg()

    seen_search_names = []
    call_count = 0

    async def mock_step(_, s, e):
        nonlocal call_count
        call_count += 1
        seen_search_names.append(e.search_name)
        if call_count == 1:
            state._entries = [MediaEntry(title = "Solo Leveling", search_name = "SL S2")]
            return True
        return False

    with patch("lydarr.tracker._step_anime", new_callable = AsyncMock, side_effect = mock_step):
        await track_media(cfg, state, entry)

    assert seen_search_names == ["", "SL S2"]
