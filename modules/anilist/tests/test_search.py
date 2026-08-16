import pytest
from unittest.mock import AsyncMock, patch
from anilist.types import AnilistMedia, MediaType, MediaStatus, AnilistError
from anilist.search import _parse, airing_schedules, search, find_by_title


SOLO_LEVELING_MEDIA = {
    "id": 153406,
    "type": "ANIME",
    "title": {
        "romaji": "Solo Leveling Season 2 -Arise from the Shadow-",
        "english": "Solo Leveling Season 2",
    },
    "status": "FINISHED",
    "episodes": 25,
    "chapters": None,
    "nextAiringEpisode": None,
}

ONGOING_MEDIA = {
    "id": 999,
    "type": "ANIME",
    "title": {"romaji": "New Anime", "english": None},
    "status": "RELEASING",
    "episodes": None,
    "chapters": None,
    "nextAiringEpisode": {"airingAt": 1700000000, "episode": 5},
}

MANGA_MEDIA = {
    "id": 42,
    "type": "MANGA",
    "title": {"romaji": "Some Manga", "english": "Some Manga EN"},
    "status": "RELEASING",
    "episodes": None,
    "chapters": 120,
    "nextAiringEpisode": None,
}


def test_parse_finished_anime():
    m = _parse(SOLO_LEVELING_MEDIA)
    assert m.id == 153406
    assert m.title_romaji == "Solo Leveling Season 2 -Arise from the Shadow-"
    assert m.title_english == "Solo Leveling Season 2"
    assert m.media_type == MediaType.ANIME
    assert m.status == MediaStatus.FINISHED
    assert m.episodes == 25
    assert m.chapters is None
    assert m.next_airing_at is None
    assert m.next_airing_episode is None


def test_parse_ongoing_anime_with_next_episode():
    m = _parse(ONGOING_MEDIA)
    assert m.status == MediaStatus.RELEASING
    assert m.next_airing_at == 1700000000
    assert m.next_airing_episode == 5


def test_parse_manga():
    m = _parse(MANGA_MEDIA)
    assert m.media_type == MediaType.MANGA
    assert m.chapters == 120
    assert m.episodes is None


def test_parse_unknown_status():
    d = {**SOLO_LEVELING_MEDIA, "status": "MYSTERY_STATUS"}
    m = _parse(d)
    assert m.status == MediaStatus.UNKNOWN


def test_display_title_english_preferred():
    m = _parse(SOLO_LEVELING_MEDIA)
    assert m.display_title() == "Solo Leveling Season 2"


def test_display_title_romaji_fallback():
    d = {**SOLO_LEVELING_MEDIA, "title": {"romaji": "Solo Leveling S2", "english": None}}
    m = _parse(d)
    assert m.display_title() == "Solo Leveling S2"


def test_display_title_id_fallback():
    d = {**SOLO_LEVELING_MEDIA, "title": {"romaji": None, "english": None}}
    m = _parse(d)
    assert m.display_title() == f"ID:{SOLO_LEVELING_MEDIA['id']}"


@pytest.mark.asyncio
async def test_search_returns_list():
    graphql_response = {
        "Page": {
            "media": [SOLO_LEVELING_MEDIA, ONGOING_MEDIA],
        }
    }
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        results = await search("Solo Leveling", MediaType.ANIME)

    assert len(results) == 2
    assert results[0].id == 153406


@pytest.mark.asyncio
async def test_search_empty():
    graphql_response = {"Page": {"media": []}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        results = await search("Nonexistent", MediaType.ANIME)

    assert results == []


@pytest.mark.asyncio
async def test_find_by_title_exact_english_match():
    graphql_response = {"Page": {"media": [SOLO_LEVELING_MEDIA]}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        result = await find_by_title("Solo Leveling Season 2", MediaType.ANIME)

    assert result is not None
    assert result.id == 153406


@pytest.mark.asyncio
async def test_find_by_title_exact_romaji_match():
    graphql_response = {"Page": {"media": [SOLO_LEVELING_MEDIA]}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        result = await find_by_title(
            "Solo Leveling Season 2 -Arise from the Shadow-", MediaType.ANIME
        )

    assert result is not None


@pytest.mark.asyncio
async def test_find_by_title_partial_match():
    graphql_response = {"Page": {"media": [SOLO_LEVELING_MEDIA]}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        result = await find_by_title("Solo Leveling", MediaType.ANIME)

    assert result is not None


@pytest.mark.asyncio
async def test_find_by_title_no_match_returns_first():
    graphql_response = {"Page": {"media": [SOLO_LEVELING_MEDIA]}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        result = await find_by_title("Completely Different", MediaType.ANIME)

    assert result is not None
    assert result.id == 153406


@pytest.mark.asyncio
async def test_find_by_title_empty_results():
    graphql_response = {"Page": {"media": []}}
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = graphql_response):
        result = await find_by_title("Nonexistent", MediaType.ANIME)

    assert result is None


@pytest.mark.asyncio
async def test_find_by_title_colon_strip_fallback():
    """Full colon-subtitle query returns nothing; retry with pre-colon portion succeeds."""
    empty = {"Page": {"media": []}}
    hit = {"Page": {"media": [SOLO_LEVELING_MEDIA]}}
    call_count = 0

    async def mock_graphql(query, variables):
        nonlocal call_count
        call_count += 1
        return empty if call_count == 1 else hit

    with patch("anilist.search.graphql", side_effect = mock_graphql):
        result = await find_by_title(
            "Solo Leveling Season 2: Arise from the Shadow", MediaType.ANIME
        )

    assert call_count == 2
    assert result is not None
    assert result.id == 153406


@pytest.mark.asyncio
async def test_find_by_title_no_colon_no_retry():
    """Titles without a colon do not trigger a second query."""
    empty = {"Page": {"media": []}}
    call_count = 0

    async def mock_graphql(query, variables):
        nonlocal call_count
        call_count += 1
        return empty

    with patch("anilist.search.graphql", side_effect = mock_graphql):
        result = await find_by_title("Nonexistent Title", MediaType.ANIME)

    assert call_count == 1
    assert result is None


@pytest.mark.asyncio
async def test_graphql_http_error():
    with patch("anilist.http.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value = mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value = False)
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.text = "Server Error"
        mock_client.post = AsyncMock(return_value = mock_resp)

        from anilist.http import graphql as graphql_fn
        with pytest.raises(AnilistError, match = "HTTP 500"):
            await graphql_fn("query {}", {})


def _schedule_page(nodes: list[dict], has_next: bool = False) -> dict:
    return {"Page": {"pageInfo": {"hasNextPage": has_next}, "airingSchedules": nodes}}


@pytest.mark.asyncio
async def test_airing_schedules_no_ids_skips_request():
    with patch("anilist.search.graphql", new_callable = AsyncMock) as mock_gql:
        assert await airing_schedules([], 0, 100) == []
    mock_gql.assert_not_called()


@pytest.mark.asyncio
async def test_airing_schedules_parses_nodes():
    page = _schedule_page([
        {"mediaId": 21, "episode": 1174, "airingAt": 1755346560, "media": {"duration": 24}},
        {"mediaId": 99, "episode": 3, "airingAt": 1755400000, "media": None},
    ])
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = page) as mock_gql:
        eps = await airing_schedules([21, 99], 1755000000, 1756000000)

    assert [(e.media_id, e.episode, e.airing_at, e.duration) for e in eps] == [
        (21, 1174, 1755346560, 24),
        (99, 3, 1755400000, None),
    ]
    # The API's range bounds are exclusive, so the window is widened by a second.
    variables = mock_gql.call_args.args[1]
    assert variables["start"] == 1755000000 - 1
    assert variables["end"] == 1756000000 + 1
    assert variables["ids"] == [21, 99]


@pytest.mark.asyncio
async def test_airing_schedules_follows_pagination():
    pages = [
        _schedule_page([{"mediaId": 1, "episode": 1, "airingAt": 1, "media": {"duration": 24}}], has_next = True),
        _schedule_page([{"mediaId": 1, "episode": 9, "airingAt": 9, "media": {"duration": 24}}], has_next = False),
    ]
    with patch("anilist.search.graphql", new_callable = AsyncMock, side_effect = pages) as mock_gql:
        eps = await airing_schedules([1], 0, 100)

    assert [e.episode for e in eps] == [1, 9]
    assert [c.args[1]["page"] for c in mock_gql.call_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_airing_schedules_stops_at_page_cap():
    endless = _schedule_page([{"mediaId": 1, "episode": 1, "airingAt": 1, "media": None}], has_next = True)
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = endless) as mock_gql:
        eps = await airing_schedules([1], 0, 100)

    assert mock_gql.await_count == 10
    assert len(eps) == 10


@pytest.mark.asyncio
async def test_graphql_errors_in_response():
    with patch("anilist.http.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value = mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value = False)
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value = {"errors": [{"message": "Not found"}], "data": None})
        mock_client.post = AsyncMock(return_value = mock_resp)

        from anilist.http import graphql as graphql_fn
        with pytest.raises(AnilistError, match = "Not found"):
            await graphql_fn("query {}", {})


from unittest.mock import MagicMock
