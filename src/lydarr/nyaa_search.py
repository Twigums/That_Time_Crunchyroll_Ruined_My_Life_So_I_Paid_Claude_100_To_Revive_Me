"""Nyaa.si lookups for the tracker: candidate retrieval for episodes, chapter search for manga."""
import re
from collections.abc import Callable

from lydarr.matcher import EARLY_TOLERANCE
from nyaa.types import TorrentSite, Torrent, SearchParams, SortField, SortOrder
from nyaa.search import search

RSS_PAGE_SIZE = 75

_QUERY_OPERATORS = re.compile(r"[\"|()\[\]{}!?:;,~*]")
_LEADING_HYPHENS = re.compile(r"(?<!\S)-+")


def anime_params(query: str, sort: SortField = SortField.SEEDERS) -> SearchParams:
    return SearchParams(
        keyword = query,
        filters = 0,
        category = 1,
        subcategory = 2,
        sort = sort,
        order = SortOrder.DESC,
    )


def manga_params(query: str) -> SearchParams:
    return SearchParams(
        keyword = query,
        filters = 0,
        category = 3,
        subcategory = 1,
        sort = SortField.SEEDERS,
        order = SortOrder.DESC,
    )


def clean_query(text: str) -> str:
    """Strip characters Nyaa reads as search operators from a title used as a query."""
    text = _QUERY_OPERATORS.sub(" ", text)
    text = _LEADING_HYPHENS.sub(" ", text)
    return " ".join(text.split())


def _reaches_back(latest: list[Torrent], airing_at: int | None) -> bool:
    """Whether a newest-first listing still covers everything uploaded since the air date."""
    if airing_at is None:
        return False
    if len(latest) < RSS_PAGE_SIZE:
        return True
    stamps = [t.published for t in latest if t.published is not None]
    return bool(stamps) and min(stamps) <= airing_at - EARLY_TOLERANCE


async def find_candidates(query: str, numbers: list[int], airing_at: int | None,
                          listings: dict[str, list[Torrent]] | None = None) -> list[Torrent]:
    """Torrents that could be one episode of a show, for the matcher to judge.

    Normally a single request: the newest uploads for the show's name. Only when
    that listing no longer reaches back to the air date (catching up on an old
    episode) are the specific episode numbers searched as well. `listings` lets a
    caller checking several episodes of one show reuse the newest-uploads request.
    """
    if listings is not None and query in listings:
        latest = listings[query]
    else:
        latest = await search(TorrentSite.NYAA_SI, anime_params(query, SortField.ID))
        if listings is not None:
            listings[query] = latest
    found = {t.info_hash or t.id: t for t in latest}
    if not _reaches_back(latest, airing_at):
        for number in numbers:
            for t in await search(TorrentSite.NYAA_SI, anime_params(f"{query} {number:02d}")):
                found.setdefault(t.info_hash or t.id, t)
    return list(found.values())


def filter_submitters(torrents: list[Torrent], submitters: list[str]) -> list[Torrent]:
    if not submitters:
        return torrents
    lower = [s.lower() for s in submitters]
    filtered = [t for t in torrents if any(s in t.name.lower() for s in lower)]
    return filtered if filtered else torrents


async def _try_queries(queries: list[str], params_fn: Callable[[str], SearchParams]) -> list[Torrent]:
    for query in queries:
        try:
            results = await search(TorrentSite.NYAA_SI, params_fn(query))
            if results:
                return results
        except Exception:
            continue
    return []


async def search_chapter(name: str, chapter_num: int, submitters: list[str] | None = None) -> Torrent | None:
    pad3 = f"{chapter_num:03d}"
    pad2 = f"{chapter_num:02d}"
    queries = [
        f"{name} Chapter {pad3}",
        f"{name} {pad3}",
        f"{name} Chapter {pad2}",
    ]
    results = await _try_queries(queries, manga_params)
    results = [t for t in filter_submitters(results, submitters or []) if t.magnet]
    return results[0] if results else None
