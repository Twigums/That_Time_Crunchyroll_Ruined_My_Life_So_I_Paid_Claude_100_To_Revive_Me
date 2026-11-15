"""Jellyfin client: have the server scan the folder a download landed in, then look for the file.

lydarr never hands Jellyfin a path to import. It finds the show (or, for a show
Jellyfin does not know yet, the library) whose folder holds the download and
asks for that item to be scanned: the request behind "Scan for new and updated
files" in Jellyfin's own UI.
"""
from dataclasses import dataclass

import httpx

from lydarr.config import AppConfig

TIMEOUT = 20


class JellyfinError(Exception):
    """Jellyfin answered, but refused or did not understand the request."""


class JellyfinUnavailable(JellyfinError):
    """Jellyfin could not be reached, or failed on its side."""


@dataclass(frozen = True)
class Library:
    id: str
    name: str
    locations: tuple[str, ...] = ()


@dataclass(frozen = True)
class Item:
    id: str
    name: str
    path: str = ""
    series: str = ""
    season: int | None = None
    episode: int | None = None

    def label(self) -> str:
        if self.episode is None:
            return self.name or self.series
        number = f"E{self.episode:02d}" if self.season is None else f"S{self.season:02d}E{self.episode:02d}"
        return f"{self.series} {number}".strip()


@dataclass(frozen = True)
class Placement:
    """Where a path lydarr saved to sits in Jellyfin."""
    path: str = ""                      # as Jellyfin sees it; empty when it could not be worked out
    library: Library | None = None
    show: Item | None = None


# ── paths ─────────────────────────────────────────────────────────────────────

def _norm(path: str) -> str:
    path = path.replace("\\", "/")
    trimmed = path.rstrip("/")
    return trimmed or ("/" if path else "")


def _join(base: str, rest: str) -> str:
    base, rest = _norm(base), rest.strip("/")
    return f"{base.rstrip('/')}/{rest}" if rest else base


def inside(path: str, folder: str) -> bool:
    """Whether `path` is `folder` itself or lies somewhere under it."""
    path, folder = _norm(path), _norm(folder)
    if not path or not folder:
        return False
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _line_up(path: str, locations: list[str]) -> str | None:
    """Pair a library folder with the folder of `path` whose trailing names it shares.

    /media/Jellyfin/Anime and /home/me/media/Jellyfin/Anime are usually one
    folder mounted under two roots. The longest run of matching names wins; a tie
    between different answers gives none.
    """
    parts = _norm(path).split("/")
    best, answers = 0, set()
    for location in locations:
        tail = [name for name in _norm(location).split("/") if name]
        for end in range(len(parts), 0, -1):
            run = 0
            while (run < len(tail) and run < end and parts[end - 1 - run]
                   and parts[end - 1 - run].casefold() == tail[-1 - run].casefold()):
                run += 1
            if run == 0 or run < best:
                continue
            answer = _join(location, "/".join(parts[end:]))
            if run > best:
                best, answers = run, set()
            answers.add(answer)
    return answers.pop() if len(answers) == 1 else None


def to_jellyfin(path: str, path_map: list[tuple[str, str]], locations: list[str]) -> str | None:
    """The path Jellyfin knows a file lydarr saved by, or None when that cannot be worked out.

    An explicit `JELLYFIN_PATH_MAP` entry wins; otherwise a path already inside a
    library folder is taken as it is, and anything else is lined up by folder names.
    """
    for local, remote in sorted(path_map, key = lambda pair: len(_norm(pair[0])), reverse = True):
        if inside(path, local):
            return _join(remote, _norm(path)[len(_norm(local)):])
    if any(inside(path, location) for location in locations):
        return _norm(path)
    return _line_up(path, locations)


# ── the server ────────────────────────────────────────────────────────────────

async def _request(method: str, url: str, api_key: str, params: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f'MediaBrowser Client="lydarr", Token="{api_key}"'}
    async with httpx.AsyncClient(timeout = TIMEOUT) as client:
        return await client.request(method, url, params = params, headers = headers)


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _item(raw: dict) -> Item:
    return Item(
        id = str(raw["Id"]),
        name = str(raw.get("Name") or ""),
        path = str(raw.get("Path") or ""),
        series = str(raw.get("SeriesName") or ""),
        season = _number(raw.get("ParentIndexNumber")),
        episode = _number(raw.get("IndexNumber")),
    )


class Jellyfin:
    """One round of questions to the server; libraries and shows are fetched once per instance."""

    def __init__(self, url: str, api_key: str, path_map: list[tuple[str, str]] | None = None) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.path_map = list(path_map or [])
        self._libraries: list[Library] | None = None
        self._shows: dict[str, list[Item]] = {}

    async def _call(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        try:
            r = await _request(method, self.url + path, self.api_key, params)
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise JellyfinError(f"JELLYFIN_URL {self.url!r} is not a usable address ({exc})") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise JellyfinUnavailable(f"cannot reach {self.url} ({str(exc) or type(exc).__name__})") from exc
        if r.status_code == 401:
            raise JellyfinError("the API key was refused (HTTP 401); check JELLYFIN_API_KEY")
        if r.status_code == 403:
            raise JellyfinError("the API key lacks admin rights (HTTP 403)")
        if r.status_code >= 500:
            raise JellyfinUnavailable(f"HTTP {r.status_code} from {path}")
        if r.status_code >= 300:
            raise JellyfinError(f"HTTP {r.status_code} from {path}; check JELLYFIN_URL")
        return r

    async def _json(self, path: str, params: dict | None = None):
        r = await self._call("GET", path, params)
        try:
            return r.json()
        except ValueError as exc:
            raise JellyfinError(f"unreadable reply from {path}") from exc

    async def libraries(self) -> list[Library]:
        if self._libraries is None:
            raw = await self._json("/Library/VirtualFolders")
            if not isinstance(raw, list):
                raise JellyfinError("unreadable library list")
            self._libraries = [
                Library(
                    id = str(lib["ItemId"]),
                    name = str(lib.get("Name") or ""),
                    locations = tuple(str(p) for p in lib.get("Locations") or [] if p),
                )
                for lib in raw if isinstance(lib, dict) and lib.get("ItemId")
            ]
        return self._libraries

    async def _items(self, parent_id: str, kind: str) -> list[Item]:
        raw = await self._json("/Items", {
            "parentId": parent_id,
            "includeItemTypes": kind,
            "recursive": "true",
            "fields": "Path",
            "enableImages": "false",
            "enableUserData": "false",
            "enableTotalRecordCount": "false",
        })
        items = raw.get("Items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise JellyfinError("unreadable item list")
        return [_item(i) for i in items if isinstance(i, dict) and i.get("Id")]

    async def shows(self, library: Library) -> list[Item]:
        if library.id not in self._shows:
            self._shows[library.id] = await self._items(library.id, "Series")
        return self._shows[library.id]

    async def episodes(self, show: Item) -> list[Item]:
        return await self._items(show.id, "Episode")

    async def refresh(self, item_id: str) -> None:
        """Scan an item's folder for new and changed files, as Jellyfin's own UI does."""
        await self._call("POST", f"/Items/{item_id}/Refresh", {
            "metadataRefreshMode": "Default",
            "imageRefreshMode": "Default",
            "replaceAllMetadata": "false",
            "replaceAllImages": "false",
        })

    async def locate(self, path: str) -> Placement:
        libraries = await self.libraries()
        remote = to_jellyfin(path, self.path_map, [loc for lib in libraries for loc in lib.locations])
        if remote is None:
            return Placement()
        holders = [(len(_norm(loc)), lib) for lib in libraries for loc in lib.locations if inside(remote, loc)]
        if not holders:
            return Placement(path = remote)
        library = max(holders, key = lambda holder: holder[0])[1]
        shows = [show for show in await self.shows(library) if inside(remote, show.path)]
        show = max(shows, key = lambda s: len(_norm(s.path)), default = None)
        return Placement(path = remote, library = library, show = show)

    async def find(self, place: Placement) -> Item | None:
        """The episode Jellyfin lists for the file, or the folder of files, at `place.path`."""
        if place.show is None:
            return None
        return next((e for e in await self.episodes(place.show) if inside(e.path, place.path)), None)


def from_config(cfg: AppConfig) -> Jellyfin | None:
    if not cfg.jellyfin_enabled:
        return None
    return Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_path_map)
