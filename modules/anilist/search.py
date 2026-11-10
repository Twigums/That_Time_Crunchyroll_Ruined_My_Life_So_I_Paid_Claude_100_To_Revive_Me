"""AniList lookups: title search, batched fetch by id, airing schedules, prequel chains."""
from anilist.types import AiringEpisode, AnilistMedia, AnilistError, MediaType, MediaStatus
from anilist.http import graphql

_MEDIA_FIELDS = """
      id
      type
      format
      title { romaji english native }
      synonyms
      status
      episodes
      chapters
      duration
      nextAiringEpisode { airingAt episode }
      coverImage { medium }
"""

_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 15) {
    media(search: $search, type: $type, sort: SEARCH_MATCH) {""" + _MEDIA_FIELDS + """    }
  }
}
"""

_BY_ID_QUERY = """
query ($ids: [Int], $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(id_in: $ids) {""" + _MEDIA_FIELDS + """    }
  }
}
"""

_SCHEDULE_QUERY = """
query ($ids: [Int], $start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(mediaId_in: $ids, airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      mediaId
      episode
      airingAt
      media { duration }
    }
  }
}
"""

_FULL_SCHEDULE_QUERY = """
query ($ids: [Int], $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(mediaId_in: $ids, sort: TIME) {
      mediaId
      episode
      airingAt
      media { duration }
    }
  }
}
"""

_RELATIONS_QUERY = """
query ($id: Int) {
  Media(id: $id) {
    id
    relations {
      edges {
        relationType(version: 2)
        node { id type format episodes }
      }
    }
  }
}
"""

_MAX_SCHEDULE_PAGES = 20
_MAX_ID_PAGES = 10
_MAX_PREQUEL_DEPTH = 8

# Formats whose episodes a release group would count when numbering a sequel absolutely.
_SERIES_FORMATS = {"TV", "TV_SHORT", "ONA"}


def _parse(m: dict) -> AnilistMedia:
    nae = m.get("nextAiringEpisode") or {}
    title = m.get("title") or {}
    try:
        status = MediaStatus(m["status"])
    except ValueError:
        status = MediaStatus.UNKNOWN
    return AnilistMedia(
        id = m["id"],
        title_english = title.get("english"),
        title_romaji = title.get("romaji"),
        media_type = MediaType(m["type"]),
        status = status,
        episodes = m.get("episodes"),
        chapters = m.get("chapters"),
        next_airing_at = nae.get("airingAt"),
        next_airing_episode = nae.get("episode"),
        cover_image = (m.get("coverImage") or {}).get("medium"),
        title_native = title.get("native"),
        synonyms = [s for s in (m.get("synonyms") or []) if s],
        format = m.get("format"),
        duration = m.get("duration"),
    )


async def search(query: str, media_type: MediaType, *, interactive: bool = False) -> list[AnilistMedia]:
    data = await graphql(
        _SEARCH_QUERY, {"search": query, "type": media_type.value}, interactive = interactive,
    )
    return [_parse(m) for m in data["Page"]["media"]]


async def media_by_ids(media_ids: list[int]) -> list[AnilistMedia]:
    """Fetch many media in as few requests as possible (50 per request)."""
    ids = sorted(set(media_ids))
    found: list[AnilistMedia] = []
    for offset in range(0, len(ids), 50):
        chunk = ids[offset:offset + 50]
        for page in range(1, _MAX_ID_PAGES + 1):
            data = await graphql(_BY_ID_QUERY, {"ids": chunk, "page": page})
            found.extend(_parse(m) for m in data["Page"]["media"])
            if not data["Page"]["pageInfo"]["hasNextPage"]:
                break
    return found


async def airing_schedules(
    media_ids: list[int], start: int | None = None, end: int | None = None,
) -> list[AiringEpisode]:
    """Episodes airing in [start, end] for the given media; every episode when unbounded."""
    if not media_ids:
        return []
    if start is None or end is None:
        query, variables = _FULL_SCHEDULE_QUERY, {"ids": list(media_ids)}
    else:
        # AniList's airingAt_greater/_lesser bounds are exclusive.
        query, variables = _SCHEDULE_QUERY, {"ids": list(media_ids), "start": start - 1, "end": end + 1}
    episodes: list[AiringEpisode] = []
    for page in range(1, _MAX_SCHEDULE_PAGES + 1):
        data = await graphql(query, {**variables, "page": page})
        chunk = data["Page"]["airingSchedules"]
        episodes.extend(
            AiringEpisode(
                media_id = n["mediaId"],
                episode = n["episode"],
                airing_at = n["airingAt"],
                duration = (n.get("media") or {}).get("duration"),
            )
            for n in chunk
        )
        if not data["Page"]["pageInfo"]["hasNextPage"]:
            break
    return episodes


def _pick_prequel(edges: list[dict]) -> dict | None:
    prequels = [
        e["node"] for e in edges
        if e.get("relationType") == "PREQUEL" and (e.get("node") or {}).get("type") == "ANIME"
    ]
    series = [n for n in prequels if n.get("format") in _SERIES_FORMATS]
    return (series or prequels or [None])[0]


async def prequel_episode_counts(media_id: int) -> list[int]:
    """Episode counts of earlier seasons, nearest first.

    Release groups that number a sequel absolutely continue from the sum of
    these, so they tell us which episode numbers a given episode may carry.
    Movies and specials in the chain are walked through but not counted; the
    walk stops at a season whose length AniList does not know.
    """
    counts: list[int] = []
    seen = {media_id}
    current = media_id
    for _ in range(_MAX_PREQUEL_DEPTH):
        data = await graphql(_RELATIONS_QUERY, {"id": current})
        edges = ((data.get("Media") or {}).get("relations") or {}).get("edges") or []
        node = _pick_prequel(edges)
        if node is None or node["id"] in seen:
            break
        seen.add(node["id"])
        if node.get("format") in _SERIES_FORMATS:
            if not node.get("episodes"):
                break
            counts.append(int(node["episodes"]))
        current = node["id"]
    return counts


async def find_by_title(title: str, media_type: MediaType) -> AnilistMedia | None:
    results = await search(title, media_type)
    if not results and ":" in title:
        results = await search(title.rsplit(":", 1)[0].strip(), media_type)
    q = title.lower()
    for m in results:
        if (m.title_english or "").lower() == q or (m.title_romaji or "").lower() == q:
            return m
    for m in results:
        if q in (m.title_english or "").lower() or q in (m.title_romaji or "").lower():
            return m
    return results[0] if results else None


__all__ = [
    "AnilistError", "airing_schedules", "find_by_title", "media_by_ids",
    "prequel_episode_counts", "search",
]
