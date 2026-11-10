from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import pytest

from lydarr.nyaa_search import RSS_PAGE_SIZE, clean_query, find_candidates
from nyaa.parser._utils import magnet_hash, parse_timestamp
from nyaa.types import SortField, Torrent, TorrentType

AIRED = 1_800_000_000
DAY = 86400


def _torrent(n: int, published: int) -> Torrent:
    return Torrent(
        id = str(n), category = "", url = "", name = f"[Group] Show - {n:02d} [1080p]", download_url = "",
        magnet = f"magnet:?xt=urn:btih:{n:040x}&dn=x",
        size = "1 GiB", date = format_datetime(datetime.fromtimestamp(published, tz = timezone.utc)),
        seeders = 1, leechers = 0, completed = None, type = TorrentType.NORMAL,
    )


@pytest.mark.asyncio
async def test_one_request_when_the_listing_covers_the_air_date():
    latest = [_torrent(1, AIRED + 3600), _torrent(2, AIRED - 5 * DAY)]
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, return_value = latest) as mock_search:
        found = await find_candidates("Show", [4, 52], AIRED)

    assert mock_search.await_count == 1
    params = mock_search.await_args.args[1]
    assert params.keyword == "Show"
    assert params.sort == SortField.ID
    assert params.category_param == "1_2"
    assert len(found) == 2


@pytest.mark.asyncio
async def test_numbered_queries_when_the_listing_is_too_recent():
    full_page = [_torrent(n, AIRED + 30 * DAY) for n in range(RSS_PAGE_SIZE)]
    numbered = [_torrent(900, AIRED + 3600), full_page[0]]
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, side_effect = [full_page, numbered, []]) as mock_search:
        found = await find_candidates("Show", [4, 52], AIRED)

    assert [c.args[1].keyword for c in mock_search.await_args_list] == ["Show", "Show 04", "Show 52"]
    assert len(found) == RSS_PAGE_SIZE + 1


@pytest.mark.asyncio
async def test_unknown_air_date_always_searches_by_number():
    with patch("lydarr.nyaa_search.search", new_callable = AsyncMock, return_value = []) as mock_search:
        await find_candidates("Show", [7], None)
    assert mock_search.await_count == 2


def test_clean_query_removes_search_operators():
    assert clean_query("Even the Student Council Has Its Holes!") == "Even the Student Council Has Its Holes"
    assert clean_query("Re:ZERO -Starting Life in Another World-") == "Re ZERO Starting Life in Another World-"
    assert clean_query('Mahou Shoujo Ikusei Keikaku: restart') == "Mahou Shoujo Ikusei Keikaku restart"
    assert clean_query("Erai-raws") == "Erai-raws"


def test_rss_dates_are_read_as_utc():
    assert parse_timestamp("Fri, 23 Oct 2026 15:00:00 -0000") == 1792767600
    assert parse_timestamp("2026-10-23 15:00") == 1792767600
    assert parse_timestamp("") is None
    assert parse_timestamp("yesterday") is None


def test_magnet_hash_is_lowercased():
    assert magnet_hash("magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01&dn=x") == \
        "abcdef0123456789abcdef0123456789abcdef01"
    assert magnet_hash("not a magnet") == ""
