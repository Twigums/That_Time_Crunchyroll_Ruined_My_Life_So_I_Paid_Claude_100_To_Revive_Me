"""Torrent search and add routes."""
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

from lydarr.nyaa_search import anime_params, manga_params
from lydarr.release import Release, parse_release
from nyaa.search import search as nyaa_search
from nyaa.types import SearchParams, SortField, SortOrder, Torrent, TorrentSite

router = APIRouter(prefix = "/api")

_SITE_MAP: dict[str, TorrentSite] = {
    "nyaa_si": TorrentSite.NYAA_SI,
    "sukebei": TorrentSite.SUKEBEI_NYAA_SI,
    "nyaa_land": TorrentSite.NYAA_LAND,
}


def _row(t: Torrent, release: Release | None = None, verdict: str | None = None, reason: str | None = None) -> dict:
    r = release or parse_release(t.name)
    return {
        "id": t.id,
        "name": t.name,
        "size": t.size,
        "date": t.date,
        "published": t.published,
        "seeders": t.seeders,
        "leechers": t.leechers,
        "type": t.type.value,
        "magnet": t.magnet,
        "download_url": t.download_url,
        "group": r.group,
        "season": r.season,
        "episode": r.episode,
        "resolution": r.resolution,
        "hevc": r.hevc,
        "batch": r.batch,
        "verdict": verdict,
        "reason": reason,
    }


@router.get("/torrents/search")
async def search_torrents(
    request: Request,
    q: str,
    ep: int | None = None,
    site: str = "nyaa_si",
    entry: str | None = None,
    kind: str = "anime",
):
    """Search Nyaa.

    With a watchlist entry and an episode number this runs the tracker's own
    lookup and labels every result with its verdict, so the browser shows
    exactly what the tracker would pick and why.
    """
    tracker = request.app.state.tracker
    bound = request.app.state.anime_state.get(entry) if entry else None
    torrent_site = _SITE_MAP.get(site, TorrentSite.NYAA_SI)
    query = q.strip()
    is_anime_entry = bound is not None and bound.media_type != "manga"
    # The box is prefilled with the entry's own name. Left untouched, search the way the
    # tracker does (romaji and season-less titles) rather than by that literal text.
    untouched = is_anime_entry and query in ("", bound.search_name, bound.title)

    if is_anime_entry and ep is not None and torrent_site == TorrentSite.NYAA_SI:
        result = await tracker.preview(bound, ep, query = None if untouched else query)
        return {
            "judged": True,
            "episode": ep,
            "results": [_row(c.torrent, c.release, c.verdict, c.reason) for c in result.ordered()],
        }

    queries = tracker.queries(bound) if untouched else [query]
    is_manga = bound.media_type == "manga" if bound is not None else kind == "manga"
    found: dict[str, Torrent] = {}
    for text in queries:
        if ep is not None:
            text = f"{text} {ep:02d}"
        if torrent_site == TorrentSite.SUKEBEI_NYAA_SI:
            params = SearchParams(keyword = text, filters = 0, sort = SortField.SEEDERS, order = SortOrder.DESC)
        else:
            params = manga_params(text) if is_manga else anime_params(text)
        for t in await nyaa_search(torrent_site, params):
            found.setdefault(t.id, t)
    results = sorted(found.values(), key = lambda t: -t.seeders)
    return {"judged": False, "episode": ep, "results": [_row(t) for t in results]}


class AddMagnetBody(BaseModel):
    magnet: str
    name: str = ""
    entry: str | None = None
    episode: int | None = None
    published: int | None = None

    @field_validator("magnet")
    @classmethod
    def must_be_magnet(cls, v: str) -> str:
        if not v.startswith("magnet:?xt=urn:btih:"):
            raise ValueError("magnet must start with magnet:?xt=urn:btih:")
        return v


def _magnet_name(magnet: str) -> str:
    names = parse_qs(urlparse(magnet).query).get("dn") or [""]
    return names[0]


@router.post("/torrents/add")
async def add_torrent(body: AddMagnetBody, request: Request):
    """Add a torrent, saving it where the chosen watchlist entry saves its episodes."""
    bound = None
    if body.entry:
        bound = request.app.state.anime_state.get(body.entry)
        if bound is None:
            return {"ok": False, "error": f"{body.entry} is no longer in the watchlist"}
    name = body.name.strip() or _magnet_name(body.magnet)
    return await request.app.state.tracker.manual_add(body.magnet, name, bound, body.episode, body.published)
