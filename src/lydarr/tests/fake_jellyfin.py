"""A Jellyfin server in memory: libraries and shows, and the scans asked of it.

Patch `lydarr.jellyfin._request` with an instance.
"""
from urllib.parse import urlsplit

import httpx

URL = "http://jellyfin.invalid"
KEY = "test-key"


class FakeJellyfin:
    def __init__(self, libraries: dict[str, list[str]] | None = None) -> None:
        self.libraries = [
            {"Name": name, "ItemId": f"lib-{name}", "Locations": list(locations), "CollectionType": "tvshows"}
            for name, locations in (libraries or {}).items()
        ]
        self.shows: list[dict] = []
        self.scans: list[str] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False
        self.status = 0

    def add_show(self, library: str, name: str, path: str) -> str:
        show_id = f"show-{len(self.shows)}"
        self.shows.append({"Id": show_id, "Name": name, "Path": path, "Type": "Series", "library": f"lib-{library}"})
        return show_id

    async def __call__(self, method: str, url: str, api_key: str, params: dict | None = None) -> httpx.Response:
        params = dict(params or {})
        path = urlsplit(url).path
        self.calls.append((method, path, params))
        if self.down:
            raise httpx.ConnectError("connection refused")
        if self.status:
            return httpx.Response(self.status)
        if api_key != KEY:
            return httpx.Response(401)
        if method == "GET" and path == "/Library/VirtualFolders":
            return httpx.Response(200, json = self.libraries)
        if method == "GET" and path == "/Items" and params.get("includeItemTypes") == "Series":
            listed = [
                {k: v for k, v in show.items() if k != "library"}
                for show in self.shows if show["library"] == params.get("parentId")
            ]
            return httpx.Response(200, json = {"Items": listed, "TotalRecordCount": len(listed), "StartIndex": 0})
        if method == "POST" and path.startswith("/Items/") and path.endswith("/Refresh"):
            item_id = path.split("/")[2]
            if item_id not in {show["Id"] for show in self.shows} | {lib["ItemId"] for lib in self.libraries}:
                return httpx.Response(404)
            self.scans.append(item_id)
            return httpx.Response(204)
        return httpx.Response(404)
