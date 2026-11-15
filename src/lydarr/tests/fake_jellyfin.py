"""A Jellyfin server in memory: libraries, shows and episodes, and the scans asked of it.

Patch `lydarr.jellyfin._request` with an instance. `on_scan` runs whenever an item
is refreshed, so a test can make files "appear" the way a real scan would.
"""
import os
from collections.abc import Callable
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
        self.items: list[dict] = []
        self.scans: list[str] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.on_scan: Callable[[str], None] | None = None
        self.down = False
        self.status = 0

    def add_show(self, library: str, name: str, path: str) -> str:
        show_id = f"show-{len(self.items)}"
        self.items.append({"Id": show_id, "Name": name, "Path": path, "Type": "Series", "parent": f"lib-{library}"})
        return show_id

    def add_episode(self, show_id: str, path: str, season: int | None = None, episode: int | None = None) -> str:
        show = next(i for i in self.items if i["Id"] == show_id)
        episode_id = f"episode-{len(self.items)}"
        self.items.append({
            "Id": episode_id, "Name": os.path.basename(path), "Path": path, "Type": "Episode", "parent": show_id,
            "SeriesName": show["Name"], "ParentIndexNumber": season, "IndexNumber": episode,
        })
        return episode_id

    def _ancestors(self, item: dict) -> set[str]:
        found, parent = set(), item.get("parent")
        while parent:
            found.add(parent)
            above = next((i for i in self.items if i["Id"] == parent), None)
            parent = above.get("parent") if above else None
        return found

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
        if method == "GET" and path == "/Items":
            listed = [
                {k: v for k, v in item.items() if k != "parent"}
                for item in self.items
                if item["Type"] == params.get("includeItemTypes") and params.get("parentId") in self._ancestors(item)
            ]
            return httpx.Response(200, json = {"Items": listed, "TotalRecordCount": len(listed), "StartIndex": 0})
        if method == "POST" and path.startswith("/Items/") and path.endswith("/Refresh"):
            item_id = path.split("/")[2]
            known = {item["Id"] for item in self.items} | {lib["ItemId"] for lib in self.libraries}
            if item_id not in known:
                return httpx.Response(404)
            self.scans.append(item_id)
            if self.on_scan is not None:
                self.on_scan(item_id)
            return httpx.Response(204)
        return httpx.Response(404)
