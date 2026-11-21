"""Jellyfin client: have the server scan the folder a download landed in, and link to it.

lydarr never hands Jellyfin a path to import. It finds the show (or, for a show
Jellyfin does not know yet, the library) whose folder holds the download and
asks for that item to be scanned: the request behind "Scan for new and updated
files" in Jellyfin's own UI. The same lookup, down to the episode, gives the
page in Jellyfin's web client to open for a download.
"""
import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from lydarr.config import AppConfig

TIMEOUT = 20


class JellyfinError(Exception):
    """Jellyfin could not be reached, refused the request, or sent something unreadable."""


@dataclass(frozen = True)
class Library:
    id: str
    name: str
    locations: tuple[str, ...] = ()


@dataclass(frozen = True)
class Show:
    id: str
    name: str
    path: str = ""


@dataclass(frozen = True)
class Episode:
    id: str
    path: str = ""
    number: int | None = None


@dataclass(frozen = True)
class Placement:
    """Where a path lydarr saved to sits in Jellyfin."""
    path: str = ""                      # as Jellyfin sees it; empty when it could not be worked out
    library: Library | None = None
    show: Show | None = None


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


def _stem(name: str) -> str:
    root, ext = os.path.splitext(name)
    return (root if 1 < len(ext) <= 5 and " " not in ext else name).casefold()


# ── the server ────────────────────────────────────────────────────────────────

async def _request(method: str, url: str, api_key: str, params: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f'MediaBrowser Client="lydarr", Token="{api_key}"'}
    async with httpx.AsyncClient(timeout = TIMEOUT) as client:
        return await client.request(method, url, params = params, headers = headers)


class Jellyfin:
    """One round of questions to the server; libraries and shows are fetched once per instance."""

    def __init__(self, url: str, api_key: str, path_map: list[tuple[str, str]] | None = None) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.path_map = list(path_map or [])
        self._libraries: list[Library] | None = None
        self._shows: dict[str, list[Show]] = {}

    async def _call(self, method: str, path: str, params: dict | None = None) -> httpx.Response:
        try:
            r = await _request(method, self.url + path, self.api_key, params)
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise JellyfinError(f"JELLYFIN_URL {self.url!r} is not a usable address ({exc})") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise JellyfinError(f"cannot reach {self.url} ({str(exc) or type(exc).__name__})") from exc
        if r.status_code == 401:
            raise JellyfinError("the API key was refused (HTTP 401); check JELLYFIN_API_KEY")
        if r.status_code == 403:
            raise JellyfinError("the API key lacks admin rights (HTTP 403)")
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

    async def shows(self, library: Library) -> list[Show]:
        if library.id not in self._shows:
            raw = await self._json("/Items", {
                "parentId": library.id,
                "includeItemTypes": "Series",
                "recursive": "true",
                "fields": "Path",
                "enableImages": "false",
                "enableUserData": "false",
                "enableTotalRecordCount": "false",
            })
            items = raw.get("Items") if isinstance(raw, dict) else None
            if not isinstance(items, list):
                raise JellyfinError("unreadable show list")
            self._shows[library.id] = [
                Show(id = str(i["Id"]), name = str(i.get("Name") or ""), path = str(i.get("Path") or ""))
                for i in items if isinstance(i, dict) and i.get("Id")
            ]
        return self._shows[library.id]

    async def episodes(self, show: Show) -> list[Episode]:
        raw = await self._json("/Items", {
            "parentId": show.id,
            "includeItemTypes": "Episode",
            "recursive": "true",
            "fields": "Path",
            "enableImages": "false",
            "enableUserData": "false",
            "enableTotalRecordCount": "false",
        })
        items = raw.get("Items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise JellyfinError("unreadable episode list")
        return [
            Episode(
                id = str(i["Id"]),
                path = str(i.get("Path") or ""),
                number = i["IndexNumber"] if isinstance(i.get("IndexNumber"), int) else None,
            )
            for i in items if isinstance(i, dict) and i.get("Id")
        ]

    async def server_id(self) -> str:
        raw = await self._json("/System/Info/Public")
        if not isinstance(raw, dict) or not raw.get("Id"):
            raise JellyfinError("unreadable server info")
        return str(raw["Id"])

    def web_link(self, item_id: str, server_id: str) -> str:
        return f"{self.url}/web/#/details?{urlencode({'id': item_id, 'serverId': server_id})}"

    async def find(self, path: str, episode: int | None = None) -> str | None:
        """The Jellyfin item to open for a download: its episode, else its show, else its library.

        `path` is the torrent's file or folder as lydarr saved it. An episode
        whose file is that path, lies under it, or has its name (the torrent's
        name may lack the extension) wins when it is the only one; failing that,
        the show's only episode numbered `episode`.
        """
        place = await self.locate(path)
        if place.show is None:
            return place.library.id if place.library else None
        listed = await self.episodes(place.show)
        stem = _stem(os.path.basename(_norm(place.path)))
        for picks in (
            [e for e in listed if inside(e.path, place.path)],
            [e for e in listed if _stem(os.path.basename(_norm(e.path))) == stem],
            [e for e in listed if episode is not None and e.number == episode],
        ):
            if len(picks) == 1:
                return picks[0].id
            if picks:
                break
        return place.show.id

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


def from_config(cfg: AppConfig) -> Jellyfin | None:
    if not cfg.jellyfin_enabled:
        return None
    return Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_path_map)
