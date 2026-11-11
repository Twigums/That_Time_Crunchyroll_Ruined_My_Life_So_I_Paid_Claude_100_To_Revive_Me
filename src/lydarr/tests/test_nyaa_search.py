from unittest.mock import AsyncMock, patch

import pytest

from lydarr.nyaa_search import anime_params, filter_submitters, manga_params, search_chapter
from nyaa.types import SortField, Torrent, TorrentType


def _torrent(name: str, magnet: str = "magnet:?xt=urn:btih:" + "a" * 40) -> Torrent:
    return Torrent(
        id = "1", category = "", url = "", name = name, download_url = "", magnet = magnet,
        size = "50 MiB", date = "", seeders = 10, leechers = 0, completed = None, type = TorrentType.NORMAL,
    )


def test_params_target_the_english_translated_categories():
    assert anime_params("Show").category_param == "1_2"
    assert anime_params("Show").sort == SortField.SEEDERS
    assert anime_params("Show", SortField.ID).sort == SortField.ID
    assert manga_params("Book").category_param == "3_1"


def test_filter_submitters_keeps_everything_when_none_listed():
    torrents = [_torrent("[A] Book 001"), _torrent("[B] Book 001")]
    assert filter_submitters(torrents, []) == torrents


def test_filter_submitters_matches_case_insensitively():
    torrents = [_torrent("[A] Book 001"), _torrent("[TCB Scans] Book 001")]
    assert [t.name for t in filter_submitters(torrents, ["tcb scans"])] == ["[TCB Scans] Book 001"]


def test_filter_submitters_falls_back_when_nobody_matches():
    torrents = [_torrent("[A] Book 001")]
    assert filter_submitters(torrents, ["Nobody"]) == torrents


@pytest.mark.asyncio
async def test_search_chapter_tries_variants_until_one_hits():
    hit = [_torrent("[A] Book 012")]
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, side_effect = [[], hit]) as mock_search:
        found = await search_chapter("Book", 12)

    assert found.name == "[A] Book 012"
    assert [c.args[1].keyword for c in mock_search.await_args_list] == ["Book Chapter 012", "Book 012"]
    assert mock_search.await_args.args[1].category_param == "3_1"


@pytest.mark.asyncio
async def test_search_chapter_returns_none_without_results_or_magnets():
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, return_value = []):
        assert await search_chapter("Book", 1) is None
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, return_value = [_torrent("[A] Book 001", "")]):
        assert await search_chapter("Book", 1) is None
