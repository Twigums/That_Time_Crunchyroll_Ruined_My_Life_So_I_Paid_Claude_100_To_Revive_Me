"""Nyaa.si domain types: enums, dataclasses, and exceptions."""
from dataclasses import dataclass, field
from enum import Enum

from nyaa.parser._utils import magnet_hash, parse_timestamp


class TorrentSite(Enum):
    NYAA_SI = "nyaa.si"
    SUKEBEI_NYAA_SI = "sukebei.nyaa.si"
    NYAA_LAND = "nyaa.land"

    @property
    def base_url(self) -> str:
        return f"https://{self.value}"


class TorrentType(Enum):
    NORMAL = "normal"
    REMAKE = "remake"
    TRUSTED = "trusted"


class SortField(Enum):
    ID = "id"
    SIZE = "size"
    SEEDERS = "seeders"
    LEECHERS = "leechers"
    DOWNLOADS = "downloads"


class SortOrder(Enum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen = True)
class Torrent:
    id: str
    category: str
    url: str
    name: str
    download_url: str
    magnet: str
    size: str
    date: str
    seeders: int
    leechers: int
    completed: int | None
    type: TorrentType

    @property
    def published(self) -> int | None:
        return parse_timestamp(self.date)

    @property
    def info_hash(self) -> str:
        return magnet_hash(self.magnet)


@dataclass(frozen = True)
class FileEntry:
    name: str


@dataclass
class TorrentDetail:
    id: str
    title: str
    category: str
    url: str
    download_url: str
    magnet: str
    size: str
    date: str
    seeders: int
    leechers: int
    completed: int
    type: TorrentType
    uploader: str
    uploader_profile: str
    website: str | None
    info_hash: str
    files: list[FileEntry] = field(default_factory = list)
    description: str = ""


@dataclass
class SearchParams:
    keyword: str
    user: str | None = None
    category: int = 0
    subcategory: int = 0
    filters: int = 2
    page: int = 0
    sort: SortField = SortField.ID
    order: SortOrder = SortOrder.DESC

    @property
    def category_param(self) -> str:
        return f"{self.category}_{self.subcategory}"


def default_search_params(keyword: str) -> SearchParams:
    return SearchParams(keyword = keyword)


class NyaaError(Exception):
    pass


class HttpError(NyaaError):
    pass


class ParseError(NyaaError):
    pass


class NotFound(NyaaError):
    pass
