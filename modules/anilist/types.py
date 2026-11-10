"""AniList GraphQL API domain types."""
from dataclasses import dataclass, field
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
    title_native: str | None = None
    synonyms: list[str] = field(default_factory = list)
    format: str | None = None
    duration: int | None = None

    def display_title(self) -> str:
        return self.title_english or self.title_romaji or f"ID:{self.id}"

    def titles(self) -> list[str]:
        """Every Latin-script name the show goes by, most canonical first."""
        seen: set[str] = set()
        out: list[str] = []
        for name in (self.title_romaji, self.title_english, *self.synonyms):
            key = (name or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append((name or "").strip())
        return out


@dataclass(frozen = True)
class AiringEpisode:
    media_id: int
    episode: int
    airing_at: int
    duration: int | None = None


class AnilistError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AnilistRateLimited(AnilistError):
    pass


class AnilistUnavailable(AnilistError):
    pass
