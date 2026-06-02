"""Watchlist TOML I/O and thread-safe media state management."""
import asyncio
import json
import os
import tomllib
from dataclasses import dataclass, field
from functools import partial


@dataclass(frozen = True)
class MediaEntry:
    title: str
    media_type: str = "anime"
    submitters: list[str] = field(default_factory = list)
    last_chapter: int = 0
    search_name: str = ""            # overrides title for Nyaa queries if set
    deprecated: bool = False
    download_dir: str = ""           # overrides default_dir as the save base dir if set


def read_entries(path: str) -> list[MediaEntry]:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return [
            MediaEntry(
                title = m["title"],
                media_type = m.get("type", "anime"),
                submitters = m.get("submitters", []),
                last_chapter = m.get("last_chapter", 0),
                search_name = m.get("search_name", ""),
                deprecated = m.get("deprecated", False),
                download_dir = m.get("download_dir", ""),
            )
            for m in data.get("media", [])
        ]
    except FileNotFoundError:
        return []


def _write(path: str, entries: list[MediaEntry]) -> None:
    blocks = []
    for e in entries:
        subs = ", ".join(json.dumps(s) for s in e.submitters)
        lines = [
            "[[media]]",
            f"title = {json.dumps(e.title)}",
            f"type = {json.dumps(e.media_type)}",
            f"submitters = [{subs}]",
        ]
        if e.search_name:
            lines.append(f"search_name = {json.dumps(e.search_name)}")
        if e.download_dir:
            lines.append(f"download_dir = {json.dumps(e.download_dir)}")
        if e.media_type == "manga":
            lines.append(f"last_chapter = {e.last_chapter}")
        if e.deprecated:
            lines.append("deprecated = true")
        blocks.append("\n".join(lines))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding = "utf-8") as f:
        f.write("\n\n".join(blocks) + ("\n" if blocks else ""))
    os.replace(tmp, path)


class MediaState:
    def __init__(self, entries: list[MediaEntry]) -> None:
        self._lock = asyncio.Lock()
        self._entries: list[MediaEntry] = list(entries)

    def entries(self) -> list[MediaEntry]:
        return list(self._entries)

    def get(self, title: str) -> MediaEntry | None:
        return next((e for e in self._entries if e.title == title), None)

    def titles(self) -> list[str]:
        return [e.title for e in self._entries]

    def active_titles(self) -> list[str]:
        return [e.title for e in self._entries if not e.deprecated]

    async def add(self, path: str, entry: MediaEntry) -> None:
        async with self._lock:
            if not any(e.title == entry.title for e in self._entries):
                self._entries.append(entry)
            await asyncio.get_event_loop().run_in_executor(None, partial(_write, path, list(self._entries)))

    async def remove(self, path: str, title: str) -> None:
        async with self._lock:
            self._entries = [e for e in self._entries if e.title != title]
            await asyncio.get_event_loop().run_in_executor(None, partial(_write, path, list(self._entries)))

    async def update_entry(self, path: str, title: str, submitters: list[str], search_name: str, download_dir: str = "") -> None:
        async with self._lock:
            self._entries = [
                MediaEntry(e.title, e.media_type, submitters, e.last_chapter, search_name, e.deprecated, download_dir)
                if e.title == title else e
                for e in self._entries
            ]
            await asyncio.get_event_loop().run_in_executor(None, partial(_write, path, list(self._entries)))

    async def set_deprecated(self, path: str, title: str, deprecated: bool) -> None:
        async with self._lock:
            self._entries = [
                MediaEntry(e.title, e.media_type, e.submitters, e.last_chapter, e.search_name, deprecated, e.download_dir)
                if e.title == title else e
                for e in self._entries
            ]
            await asyncio.get_event_loop().run_in_executor(None, partial(_write, path, list(self._entries)))

    async def update_last_chapter(self, path: str, title: str, chapter: int) -> None:
        async with self._lock:
            self._entries = [
                MediaEntry(e.title, e.media_type, e.submitters, chapter, e.search_name, e.deprecated, e.download_dir)
                if e.title == title else e
                for e in self._entries
            ]
            await asyncio.get_event_loop().run_in_executor(None, partial(_write, path, list(self._entries)))
