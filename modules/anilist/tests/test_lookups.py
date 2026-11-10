import pytest
from unittest.mock import AsyncMock, patch

from anilist.search import _parse, airing_schedules, media_by_ids, prequel_episode_counts

FINISHED = {
    "id": 153406,
    "type": "ANIME",
    "title": {"romaji": "Solo Leveling Season 2 -Arise from the Shadow-", "english": "Solo Leveling Season 2"},
    "status": "FINISHED",
    "episodes": 25,
    "chapters": None,
    "nextAiringEpisode": None,
}

ONGOING = {
    "id": 999,
    "type": "ANIME",
    "title": {"romaji": "New Anime", "english": "Fresh Anime", "native": "新しい"},
    "status": "RELEASING",
    "episodes": None,
    "chapters": None,
    "nextAiringEpisode": {"airingAt": 1700000000, "episode": 5},
    "format": "TV",
    "duration": 24,
    "synonyms": ["Alias One", None, "Alias Two"],
}


def _media_page(media: list[dict], has_next: bool = False) -> dict:
    return {"Page": {"pageInfo": {"hasNextPage": has_next}, "media": media}}


def _schedule_page(nodes: list[dict], has_next: bool = False) -> dict:
    return {"Page": {"pageInfo": {"hasNextPage": has_next}, "airingSchedules": nodes}}


def _relations(*edges: tuple[str, int, str, int | None]) -> dict:
    return {"Media": {"id": 0, "relations": {"edges": [
        {"relationType": kind, "node": {"id": node_id, "type": "ANIME", "format": fmt, "episodes": eps}}
        for kind, node_id, fmt, eps in edges
    ]}}}


def _chain(pages: dict[int, dict]):
    async def fake(query, variables, **_):
        return pages[variables["id"]]
    return fake


def test_parse_reads_extended_fields():
    m = _parse(ONGOING)
    assert m.format == "TV"
    assert m.duration == 24
    assert m.title_native == "新しい"
    assert m.synonyms == ["Alias One", "Alias Two"]
    assert m.titles() == ["New Anime", "Fresh Anime", "Alias One", "Alias Two"]


def test_parse_tolerates_missing_extended_fields():
    m = _parse(FINISHED)
    assert m.format is None
    assert m.synonyms == []
    assert m.titles() == ["Solo Leveling Season 2 -Arise from the Shadow-", "Solo Leveling Season 2"]


@pytest.mark.asyncio
async def test_media_by_ids_uses_one_request_for_many_ids():
    page = _media_page([FINISHED, ONGOING])
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = page) as mock_gql:
        result = await media_by_ids([999, 153406, 999])

    assert sorted(m.id for m in result) == [999, 153406]
    assert mock_gql.call_count == 1
    assert mock_gql.call_args[0][1]["ids"] == [999, 153406]


@pytest.mark.asyncio
async def test_media_by_ids_chunks_past_fifty():
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = _media_page([])) as mock_gql:
        await media_by_ids(list(range(1, 121)))

    assert [len(c[0][1]["ids"]) for c in mock_gql.call_args_list] == [50, 50, 20]


@pytest.mark.asyncio
async def test_airing_schedules_unbounded_omits_range():
    page = _schedule_page([{"mediaId": 7, "episode": 1, "airingAt": 100, "media": {"duration": 24}}])
    with patch("anilist.search.graphql", new_callable = AsyncMock, return_value = page) as mock_gql:
        result = await airing_schedules([7])

    assert [e.episode for e in result] == [1]
    assert "start" not in mock_gql.call_args[0][1]


@pytest.mark.asyncio
async def test_prequel_counts_walk_the_chain_nearest_first():
    pages = {
        3: _relations(("PREQUEL", 2, "TV", 24), ("SIDE_STORY", 9, "ONA", 6)),
        2: _relations(("SEQUEL", 3, "TV", 12), ("PREQUEL", 1, "TV", 12)),
        1: _relations(("SEQUEL", 2, "TV", 24)),
    }
    with patch("anilist.search.graphql", side_effect = _chain(pages)):
        assert await prequel_episode_counts(3) == [24, 12]


@pytest.mark.asyncio
async def test_prequel_counts_skip_movies_but_keep_walking():
    pages = {
        3: _relations(("PREQUEL", 2, "MOVIE", 1)),
        2: _relations(("PREQUEL", 1, "TV", 13)),
        1: _relations(),
    }
    with patch("anilist.search.graphql", side_effect = _chain(pages)):
        assert await prequel_episode_counts(3) == [13]


@pytest.mark.asyncio
async def test_prequel_counts_stop_at_unknown_length():
    pages = {2: _relations(("PREQUEL", 1, "TV", None))}
    with patch("anilist.search.graphql", side_effect = _chain(pages)):
        assert await prequel_episode_counts(2) == []


@pytest.mark.asyncio
async def test_prequel_counts_survive_cycles():
    pages = {2: _relations(("PREQUEL", 1, "TV", 12)), 1: _relations(("PREQUEL", 2, "TV", 12))}
    with patch("anilist.search.graphql", side_effect = _chain(pages)):
        assert await prequel_episode_counts(2) == [12]
