"""AniList search and best-match lookup functions."""
from anilist.types import AiringEpisode, AnilistMedia, AnilistError, MediaType, MediaStatus
from anilist.http import graphql

_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 15) {
    media(search: $search, type: $type, sort: SEARCH_MATCH) {
      id
      type
      title { romaji english }
      status
      episodes
      chapters
      nextAiringEpisode { airingAt episode }
      coverImage { medium }
    }
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

_MAX_SCHEDULE_PAGES = 10


def _parse(m: dict) -> AnilistMedia:
    nae = m.get("nextAiringEpisode") or {}
    try:
        status = MediaStatus(m["status"])
    except ValueError:
        status = MediaStatus.UNKNOWN
    return AnilistMedia(
        id = m["id"],
        title_english = m["title"].get("english"),
        title_romaji = m["title"].get("romaji"),
        media_type = MediaType(m["type"]),
        status = status,
        episodes = m.get("episodes"),
        chapters = m.get("chapters"),
        next_airing_at = nae.get("airingAt"),
        next_airing_episode = nae.get("episode"),
        cover_image = (m.get("coverImage") or {}).get("medium"),
    )


async def search(query: str, media_type: MediaType) -> list[AnilistMedia]:
    data = await graphql(_SEARCH_QUERY, {"search": query, "type": media_type.value})
    return [_parse(m) for m in data["Page"]["media"]]


async def airing_schedules(media_ids: list[int], start: int, end: int) -> list[AiringEpisode]:
    """Episodes airing in [start, end] for the given media, as unix timestamps."""
    if not media_ids:
        return []
    # AniList's airingAt_greater/_lesser bounds are exclusive.
    variables = {"ids": list(media_ids), "start": start - 1, "end": end + 1}
    episodes: list[AiringEpisode] = []
    for page in range(1, _MAX_SCHEDULE_PAGES + 1):
        data = await graphql(_SCHEDULE_QUERY, {**variables, "page": page})
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
