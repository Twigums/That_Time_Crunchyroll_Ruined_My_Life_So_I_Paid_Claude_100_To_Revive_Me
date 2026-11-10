import asyncio
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from anilist.types import AiringEpisode, AnilistError, AnilistMedia, AnilistRateLimited, MediaStatus, MediaType
from lydarr.file_manager import MediaEntry, MediaState, read_entries
from lydarr.media_store import MediaStore, REFRESH_INTERVAL

NOW = int(time.time())


def _media(media_id: int, title: str, **extra) -> AnilistMedia:
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
        **extra,
    )


def _store(tmp_path, entries: list[MediaEntry]) -> tuple[MediaStore, MediaState, str]:
    anime_file = str(tmp_path / "anime.toml")
    state = MediaState(entries)
    return MediaStore(str(tmp_path / "cache.json"), state, anime_file), state, anime_file


@contextmanager
def _anilist(media = None, schedule = None, found = None, prequels = None):
    with patch("lydarr.media_store.media_by_ids", new_callable = AsyncMock, return_value = media or []) as by_ids, \
         patch("lydarr.media_store.airing_schedules", new_callable = AsyncMock, return_value = schedule or []) as sched, \
         patch("lydarr.media_store.find_by_title", new_callable = AsyncMock, return_value = found) as find, \
         patch("lydarr.media_store.prequel_episode_counts", new_callable = AsyncMock, return_value = prequels or []) as pre:
        yield {"by_ids": by_ids, "schedule": sched, "find": find, "prequels": pre}


@pytest.mark.asyncio
async def test_refresh_batches_all_tracked_ids_into_one_lookup(tmp_path):
    entries = [MediaEntry(title = "A", anilist_id = 1), MediaEntry(title = "B", anilist_id = 2)]
    store, _, _ = _store(tmp_path, entries)

    with _anilist(media = [_media(1, "A"), _media(2, "B")]) as api:
        assert await store.refresh() is True

    api["by_ids"].assert_awaited_once_with([1, 2])
    api["find"].assert_not_awaited()
    assert api["schedule"].await_count == 1
    assert store.info(entries[0]).display_title() == "A"


@pytest.mark.asyncio
async def test_refresh_resolves_missing_ids_once_and_saves_them(tmp_path):
    store, state, anime_file = _store(tmp_path, [MediaEntry(title = "Frieren")])

    with _anilist(media = [_media(77, "Frieren")], found = _media(77, "Frieren")) as api:
        await store.refresh()
        api["find"].assert_awaited_once()

    assert state.get("Frieren").anilist_id == 77
    assert read_entries(anime_file)[0].anilist_id == 77


@pytest.mark.asyncio
async def test_unresolvable_titles_are_reported_and_not_retried_immediately(tmp_path):
    store, _, _ = _store(tmp_path, [MediaEntry(title = "Nope")])

    with _anilist() as api:
        await store.refresh()
        assert store.needs_refresh() is False
        assert api["find"].await_count == 1

    assert store.status()["unresolved"] == ["Nope"]


@pytest.mark.asyncio
async def test_cache_survives_restart_without_network(tmp_path):
    entry = MediaEntry(title = "A", anilist_id = 1)
    store, state, anime_file = _store(tmp_path, [entry])
    rows = [AiringEpisode(media_id = 1, episode = 5, airing_at = NOW + 3600)]

    with _anilist(media = [_media(1, "A", cover_image = "c.jpg")], schedule = rows, prequels = [24]):
        await store.refresh()

    reopened = MediaStore(str(tmp_path / "cache.json"), state, anime_file)
    reopened.load()
    assert reopened.info(entry).cover_image == "c.jpg"
    assert reopened.next_episode(entry, NOW) == (5, NOW + 3600)
    assert reopened.prequels(entry) == [24]
    assert reopened.needs_refresh() is False


@pytest.mark.asyncio
async def test_failed_refresh_keeps_stale_data_and_backs_off(tmp_path):
    entry = MediaEntry(title = "A", anilist_id = 1)
    store, _, _ = _store(tmp_path, [entry])

    with _anilist(media = [_media(1, "A")]):
        await store.refresh()
    store._refreshed_at -= REFRESH_INTERVAL + 1
    assert store.needs_refresh() is True

    with _anilist() as api:
        api["by_ids"].side_effect = AnilistError("HTTP 500: boom")
        assert await store.refresh() is False

    assert store.info(entry) is not None
    assert store.status()["error"] == "HTTP 500: boom"
    assert store.needs_refresh() is False


@pytest.mark.asyncio
async def test_rate_limit_sets_retry_from_server_hint(tmp_path):
    store, _, _ = _store(tmp_path, [MediaEntry(title = "A", anilist_id = 1)])

    with _anilist() as api:
        api["by_ids"].side_effect = AnilistRateLimited("rate limited", 90)
        await store.refresh()

    assert 80 <= store.status()["retry_in"] <= 90


@pytest.mark.asyncio
async def test_concurrent_refreshes_share_one_request(tmp_path):
    store, _, _ = _store(tmp_path, [MediaEntry(title = "A", anilist_id = 1)])

    with _anilist(media = [_media(1, "A")]) as api:
        await asyncio.gather(store.refresh(), store.refresh(), store.refresh())

    assert api["by_ids"].await_count == 1


@pytest.mark.asyncio
async def test_new_entry_triggers_refresh_even_when_cache_is_fresh(tmp_path):
    store, state, anime_file = _store(tmp_path, [MediaEntry(title = "A", anilist_id = 1)])
    with _anilist(media = [_media(1, "A")]):
        await store.refresh()
    assert store.needs_refresh() is False

    await state.add(anime_file, MediaEntry(title = "B", anilist_id = 2))
    store.seed(_media(2, "B"))
    assert store.needs_refresh() is True


@pytest.mark.asyncio
async def test_prequels_are_fetched_once_per_show(tmp_path):
    entry = MediaEntry(title = "A", anilist_id = 1)
    store, _, _ = _store(tmp_path, [entry])

    with _anilist(media = [_media(1, "A")], prequels = [12, 12]) as api:
        await store.refresh()
        await store.refresh(force = True)
        assert api["prequels"].await_count == 1

    assert store.prequels(entry) == [12, 12]


@pytest.mark.asyncio
async def test_window_extension_fetches_only_the_missing_range(tmp_path):
    store, _, _ = _store(tmp_path, [MediaEntry(title = "A", anilist_id = 1)])
    with _anilist(media = [_media(1, "A")]):
        await store.refresh()
    lo, hi = store._window

    later = [AiringEpisode(media_id = 1, episode = 30, airing_at = hi + 500)]
    with _anilist(schedule = later) as api:
        await store.ensure_window(lo + 10, hi - 10)
        api["schedule"].assert_not_awaited()
        await store.ensure_window(lo + 10, hi + 1000)
        api["schedule"].assert_awaited_once_with([1], hi, hi + 1000)
        await store.ensure_window(lo + 10, hi + 1000)
        assert api["schedule"].await_count == 1

    assert store.calendar(hi, hi + 1000) == [
        {"media_id": 1, "episode": 30, "airing_at": hi + 500, "duration": None, "title": "A"},
    ]


@pytest.mark.asyncio
async def test_rescheduled_episode_replaces_the_old_air_time(tmp_path):
    entry = MediaEntry(title = "A", anilist_id = 1)
    store, _, _ = _store(tmp_path, [entry])

    with _anilist(media = [_media(1, "A")], schedule = [AiringEpisode(1, 5, NOW + 100)]):
        await store.refresh()
    with _anilist(media = [_media(1, "A")], schedule = [AiringEpisode(1, 5, NOW + 700_000)]):
        await store.refresh(force = True)

    assert store.episodes(entry) == [(5, NOW + 700_000)]


@pytest.mark.asyncio
async def test_removed_entries_are_pruned_from_the_cache(tmp_path):
    store, state, anime_file = _store(tmp_path, [
        MediaEntry(title = "A", anilist_id = 1), MediaEntry(title = "B", anilist_id = 2),
    ])
    with _anilist(media = [_media(1, "A"), _media(2, "B")]):
        await store.refresh()
        await state.remove(anime_file, "B")
        await store.refresh(force = True)

    assert 2 not in store._media


@pytest.mark.asyncio
async def test_deprecated_and_manga_entries_are_not_scheduled(tmp_path):
    store, _, _ = _store(tmp_path, [
        MediaEntry(title = "A", anilist_id = 1),
        MediaEntry(title = "Old", anilist_id = 2, deprecated = True),
        MediaEntry(title = "Book", anilist_id = 3, media_type = "manga"),
    ])
    with _anilist(media = [_media(1, "A"), _media(2, "Old"), _media(3, "Book")]) as api:
        await store.refresh()

    api["by_ids"].assert_awaited_once_with([1, 2, 3])
    assert api["schedule"].await_args.args[0] == [1]


def test_corrupt_cache_is_ignored(tmp_path):
    (tmp_path / "cache.json").write_text("{not json")
    store, _, _ = _store(tmp_path, [])
    store.load()
    assert store.status()["as_of"] is None
