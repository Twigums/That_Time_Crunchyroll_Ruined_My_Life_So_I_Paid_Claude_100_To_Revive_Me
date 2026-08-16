"""AniList GraphQL API domain types."""
from dataclasses import dataclass
from enum import Enum


class MediaType(Enum):
    ANIME = "ANIME"
    MANGA = "MANGA"


class MediaStatus(Enum):
    FINISHED = "FINISHED"
    RELEASING = "RELEASING"
    NOT_YET_RELEASED = "NOT_YET_RELEASED"
    CANCELLED = "CANCELLED"
    HIATUS = "HIATUS"
    UNKNOWN = "UNKNOWN"


@dataclass
class AnilistMedia:
    id: int
    title_english: str | None
    title_romaji: str | None
    media_type: MediaType
    status: MediaStatus
    episodes: int | None
    chapters: int | None
    next_airing_at: int | None
    next_airing_episode: int | None
    cover_image: str | None = None

    def display_title(self) -> str:
        return self.title_english or self.title_romaji or f"ID:{self.id}"


@dataclass(frozen = True)
class AiringEpisode:
    media_id: int
    episode: int
    airing_at: int
    duration: int | None = None


class AnilistError(Exception):
    pass
