# lydarr-py — Claude Code Guide

## What this is

Python port of [lydarr](https://github.com/Twigums/lydarr) (Haskell). Daemon that tracks airing anime and releasing manga, searches Nyaa.si for subbed torrents/scanlations, and adds them to Transmission. Includes a web UI for searching media, managing the watchlist, and browsing torrent results manually.

The `nyaa/` package is a **first-party inline port** of the Haskell library at `/home/twi/git/nyaa-hs`. Do not import external nyaa Python libraries — reimplement from the Haskell source.

The `anilist/` package is a lightweight GraphQL client for the [AniList API](https://anilist.co/graphiql), used for anime and manga status, episode air times, and web UI search.

See `plan.md` for the full roadmap.

---

## Package layout

```
lydarr-py/
├── anime.toml              # Watchlist: [[media]] blocks with title, type, submitters
├── modules/
│   ├── nyaa/               # Port of nyaa-hs — Nyaa.si scraping + search
│   │   ├── types.py        # Torrent, SearchParams, NyaaError, enums
│   │   ├── categories.py   # Category map lookups
│   │   ├── magnet.py       # magnet_builder()
│   │   ├── http.py         # Async httpx fetchers
│   │   ├── search.py       # Public API: search(), get_torrent(), etc.
│   │   └── parser/
│   │       ├── _utils.py   # Shared helpers: last_segment(), read_int()
│   │       ├── rss.py      # RSS feed → list[Torrent]
│   │       └── html.py     # HTML listing/detail → list[Torrent] / TorrentDetail
│   └── anilist/            # AniList GraphQL client — media search, status, and episode air times
│       ├── types.py        # AnilistMedia, AiringEpisode, MediaType, MediaStatus, AnilistError
│       ├── http.py         # graphql() POST helper
│       └── search.py       # search(), find_by_title() — retries with pre-colon substring when full title returns no results; airing_schedules() batches every tracked show's episode times for a date range in one paginated query
└── src/
    └── lydarr/
        ├── config.py       # AppConfig from env vars (Transmission URL/auth, media file, download dir)
        ├── torrent_client.py  # is_client_up(), wait_for_client(), add_magnet(), _rpc() via Transmission JSON-RPC
        ├── nyaa_search.py  # search_episode(), search_chapter(): query variants, filter_submitters(), prefer_hevc()
        ├── file_manager.py # MediaEntry(title, type, submitters, deprecated, download_dir, check_delay), MediaState (get/remove/update), read_entries(), TOML I/O
        ├── tracker.py      # track_media(cfg, state, entry) async coroutine; dispatches anime vs manga; downloads into entry.download_dir if set, else a per-title subdir under LYDARR_DEFAULT_DIR named from AniList display title (_safe_dirname strips invalid path chars); searches Nyaa entry.check_delay minutes after air time, falling back to DEFAULT_CHECK_DELAY (60)
        ├── web/            # FastAPI web UI
        │   ├── app.py      # create_app(cfg, state); app.state holds cfg, anime_state, daemon_task
        │   ├── routes/
        │   │   ├── anime.py    # /api/anime/search|list|add|remove|deprecate|reactivate|submitters|status|calendar (status and calendar ranges cached + single-flight via _cached() to spare AniList under multiple clients)
        │   │   ├── torrents.py # /api/torrents/search, /api/torrents/add
        │   │   └── daemon.py   # /api/daemon/status|start|stop; /api/rtorrent/status; /api/transmission/start|stop
        │   └── static/
        │       └── index.html  # Single-page UI: 3 resizable panels, floating Start/Stop FAB; middle panel has Watchlist and Calendar tabs (month/week episode grid, per-show colours, click an episode to search it)
        └── __main__.py     # asyncio.run(main()); starts daemon + optional web server; --host/--port flags
```

---

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Concurrency | `asyncio` + `asyncio.gather` | One coroutine per title, I/O-bound throughout |
| HTTP | `httpx` async client | Handles AniList GraphQL, Nyaa bytes, and Transmission RPC |
| HTML parsing | `beautifulsoup4` + `lxml` backend | Direct equivalent of html-conduit |
| XML/RSS parsing | `defusedxml` | Safe XML; equivalent of xml-conduit |
| Torrent client | Transmission JSON-RPC | Lightweight, simple API, no extra binary needed |
| Watchlist format | TOML (`anime.toml`) | Human-editable, supports per-title metadata (type, submitters, search_name) |
| Web framework | FastAPI | Async-native, minimal |
| Type annotations | Full throughout | Dataclasses for records, `Enum` for sum types |
| Anime/manga scheduling | AniList GraphQL API | Episode air times, status, and chapter counts; single API for all media |
| Transmission stop | `session-close` RPC | Graceful shutdown via existing RPC channel; no subprocess or PID files |

---

## Watchlist format

```toml
[[media]]
title = "Frieren: Beyond Journey's End"
type = "anime"
submitters = ["SubsPlease", "Erai-raws"]
check_delay = 90       # search Nyaa 90 min after the episode airs (default: 60)

[[media]]
title = "That Time I Got Reincarnated as a Slime Season 4"
type = "anime"
submitters = []
search_name = "Tensei Shitara Slime Datta Ken 4th Season"  # Nyaa query override

[[media]]
title = "One Piece"
type = "manga"
submitters = []        # empty = accept all uploaders
last_chapter = 1100   # daemon resumes from next chapter
deprecated = true     # hidden in UI; skipped by daemon; can be reactivated
```

Fields:
- `title` — used as the AniList search query; also the display name throughout
- `type` — `"anime"` or `"manga"` (default: `"anime"`)
- `submitters` — case-insensitive substring match against torrent title; falls back to all results if empty or no match
- `last_chapter` — manga only; daemon downloads chapters after this number
- `search_name` — optional override for Nyaa lookups only; AniList always uses `title` directly
- `check_delay` — anime only; minutes to wait after AniList's air time before searching Nyaa. Unset/`0` uses `DEFAULT_CHECK_DELAY` (60). Editable in the web UI (auto-populated with the default)
- `download_dir` — optional per-entry override for the full save path (including the title folder, e.g. `/media/Anime/MARRIAGETOXIN`); empty falls back to `LYDARR_DEFAULT_DIR/<AniList display title>`. Editable in the web UI (auto-populated with the full default path)
- `deprecated` — optional boolean (default `false`); entry is preserved but hidden/inactive

Read with stdlib `tomllib`; written with a minimal custom serialiser (no extra dependency).

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANIME_FILE` | `anime.toml` | Path to watchlist file |
| `TRANSMISSION_URL` | `http://localhost:9091/transmission/rpc` | Transmission RPC endpoint |
| `TRANSMISSION_USER` | unset | Transmission username (if auth enabled) |
| `TRANSMISSION_PASS` | unset | Transmission password (if auth enabled) |
| `LYDARR_DEFAULT_DIR` | `~/Downloads` | Download directory passed to Transmission |
| `LYDARR_USER` | unset | Web UI username for HTTP Basic Auth; leave unset to disable auth |
| `LYDARR_PASS` | unset | Web UI password for HTTP Basic Auth; required when `LYDARR_USER` is set |
| `LYDARR_WEB` | unset | Set to `1` to enable web UI alongside daemon |
| `LYDARR_WEB_HOST` | `0.0.0.0` | Web UI bind address (overridden by `--host`) |
| `LYDARR_WEB_PORT` | `8080` | Web UI port (overridden by `--port`) |

---

## Running

```bash
# Install deps
uv sync

# Run daemon only
uv run python -m lydarr

# Run daemon + web UI
LYDARR_WEB=1 uv run python -m lydarr

# Web UI only (for manual torrent browsing, no tracking)
uv run python -m lydarr --web-only

# Bind to localhost (e.g. behind a reverse proxy)
uv run python -m lydarr --web-only --host 127.0.0.1 --port 8080
```

---

## Conventions

- **snake_case** everywhere — functions, variables, modules
- **No comments** unless the why is non-obvious (a hidden constraint, workaround, etc.)
- **Dataclasses** for all record types; `frozen=True` where the object shouldn't mutate
- **Custom exceptions** (`NyaaError`, `AnilistError`) for domain errors; catch at the tracker boundary
- **`asyncio.sleep`** instead of `time.sleep` — all loops are async
- Prefer returning `None` over raising when a "not found" is a normal outcome (e.g., no torrent yet)

---

## Haskell → Python reference

| Haskell | Python |
|---|---|
| `mapConcurrently_` | `asyncio.gather(*coros)` |
| `threadDelay n` | `await asyncio.sleep(n)` |
| `TVar [Text]` | `MediaState` class with `asyncio.Lock` |
| `try @SomeException` | `try: ... except Exception:` |
| `Either NyaaError a` | raise `NyaaError` / return value |
| `Maybe a` | `a \| None` |
| `fromMaybe def val` | `val or default` |
| `T.isInfixOf` | `substring in string` |
| `T.toLower` | `str.lower()` |
| `printf "%02d" n` | `f"{n:02d}"` |
