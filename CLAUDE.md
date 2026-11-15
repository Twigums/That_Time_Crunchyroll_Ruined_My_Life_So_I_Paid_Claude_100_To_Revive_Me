# lydarr-py — Claude Code Guide

## What this is

Python port of [lydarr](https://github.com/Twigums/lydarr) (Haskell). It tracks airing anime (and, experimentally, manga), finds each new episode on Nyaa.si and adds it to Transmission. A web UI covers adding shows, the watchlist, a calendar, a torrent browser and an activity log.

The `nyaa/` package is a **first-party inline port** of the Haskell library `nyaa-hs`. Do not import external nyaa Python libraries — reimplement from the Haskell source.

The `anilist/` package is a GraphQL client for the [AniList API](https://anilist.co/graphiql).

`README.md` is the user-facing manual; keep it in step with behaviour changes.

---

## Package layout

```
lydarr-py/
├── anime.example.toml      # Every watchlist field, annotated (the real anime.toml is the user's and git-ignored)
├── lydarr.service          # systemd user unit template
├── modules/
│   ├── nyaa/               # Port of nyaa-hs — Nyaa.si scraping + search
│   │   ├── types.py        # Torrent (+ .published unix time, .info_hash), SearchParams, NyaaError, enums
│   │   ├── categories.py   # Category map lookups
│   │   ├── magnet.py       # magnet_builder()
│   │   ├── http.py         # Async httpx fetchers
│   │   ├── search.py       # Public API: search(), get_torrent(), etc.
│   │   └── parser/
│   │       ├── _utils.py   # last_segment(), read_int(), parse_timestamp() (Nyaa dates are UTC), magnet_hash()
│   │       ├── rss.py      # RSS feed → list[Torrent]
│   │       └── html.py     # HTML listing/detail → list[Torrent] / TorrentDetail
│   └── anilist/
│       ├── types.py        # AnilistMedia, AiringEpisode, enums, AnilistError / AnilistRateLimited / AnilistUnavailable
│       ├── http.py         # graphql(): ONE gate for all requests — serialised, spaced to the advertised limit, 429 pauses every caller, outages open a circuit breaker
│       └── search.py       # search(), find_by_title(), media_by_ids() (50 per request), airing_schedules(), prequel_episode_counts()
└── src/lydarr/
    ├── config.py           # AppConfig from env vars (+ .env)
    ├── file_manager.py     # MediaEntry, MediaState (in-memory + atomic TOML writes), read_entries(), WatchlistError
    ├── paths.py            # resolve_download_dir(): the ONE save-path formula (tracker, UI and manual adds)
    ├── media_store.py      # MediaStore: disk-backed AniList cache (media, schedules, prequel counts); refresh every 6h in a few batched requests; serves stale data with backoff
    ├── release.py          # parse_release(name) → Release(group, titles, season, episode, version, resolution, hevc, batch, special)
    ├── matcher.py          # evaluate(target, torrents, now, policy) → MatchResult: hard gates, then submitter/HEVC waits, then ranking; near misses for review
    ├── nyaa_search.py      # find_candidates() (one newest-first request per check; numbered queries only for catch-up), search_chapter() for manga
    ├── state.py            # TrackerState: per-episode records, learned offsets, activity log, torrents to reap, tracker on/off; JSON on disk
    ├── torrent_client.py   # Transmission JSON-RPC: add_magnet() → AddedTorrent, torrent_progress() → TorrentStatus, remove_torrents(); all by info hash
    ├── jellyfin.py         # Jellyfin client: locate() a path's library and show, refresh() an item; to_jellyfin() path mapping
    ├── tracker.py          # Tracker: tick() scheduler, review/approve/skip/retry, manual_add(), preview(), reap(), scan_jellyfin(), manga loop
    ├── __main__.py         # CLI: `lydarr` (web + tracker), `--no-web`, `lydarr check`
    ├── web/
    │   ├── app.py          # create_app(cfg, state, store=, tstate=, tracker=, resume_tracker=); error handlers; CSP
    │   ├── routes/
    │   │   ├── anime.py    # /api/anime/list|search|add|remove|deprecate|reactivate|update|backfill|refresh|calendar
    │   │   ├── torrents.py # /api/torrents/search (optionally judged by the tracker), /api/torrents/add
    │   │   ├── activity.py # /api/activity, /api/activity/approve|skip|retry
    │   │   └── daemon.py   # /api/status, /api/daemon/*, /api/transmission/*, /api/power/start|stop
    │   └── static/         # index.html, app.css, js/*.js (ES modules, no build step, no inline scripts)
    └── tests/
        ├── conftest.py     # autouse fixture that blocks AniList, Nyaa, Transmission and Jellyfin
        ├── fake_jellyfin.py # FakeJellyfin: libraries and shows in memory; records scans
        └── webrig.py       # WebRig: a real app over in-memory AniList data, for route tests
```

---

## How the pieces fit

- **One source of AniList data.** Only `MediaStore.refresh()` (and the interactive search route) call AniList. The tracker, `/api/anime/list` and the calendar read the store. Never add a per-entry or per-request AniList call.
- **One scheduler.** `Tracker.tick()` runs every minute. For each active anime it walks the cached schedule and checks every episode that has aired (plus `check_delay`), is in scope (aired after the show's `since`, or `backfill`), and is not settled. Each episode has its own retry timer and gives up after 14 days. Entries are re-read from `MediaState` every tick, so edits, removals and archiving apply immediately.
- **Matching is gated, then ranked.** A torrent must be uploaded after the air time (24 h tolerance), be the same series, be a single regular episode, and carry a number that fits (AniList's, or offset by earlier seasons' lengths, a learned per-group offset, or `episode_offset`). Wrong-number releases in the episode's own week become near misses → `review` status → the Activity tab.
- **One save-path formula.** `paths.resolve_download_dir(cfg, entry, info)`: the entry's `download_dir`, else `LYDARR_DEFAULT_DIR/<AniList display title>`. If the folder cannot be created the add fails and is retried; it never falls back to another folder.
- **Torrents are identified by info hash**, never by Transmission's session-scoped numeric id.
- **Finished downloads are scanned into Jellyfin.** `Tracker.housekeeping()` runs `reap()` and `scan_jellyfin()` every 30 s, tracker on or off. When `JELLYFIN_URL` and `JELLYFIN_API_KEY` are set, `reap()` lines up each completed torrent's path (Transmission's `downloadDir/name`) in memory, and `scan_jellyfin()` refreshes the Jellyfin show whose folder holds it `SCAN_DELAY` later (its library for a show Jellyfin does not list yet), once per show. It sends the request once and does not check the result; failures are only logged. lydarr never sends Jellyfin a path; `jellyfin.to_jellyfin()` translates lydarr's path to Jellyfin's view (`JELLYFIN_PATH_MAP`, else the same path, else folder names lined up with a library folder).

---

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Concurrency | `asyncio`, one scheduler loop | I/O-bound; per-episode timers instead of one sleeping coroutine per title |
| HTTP | `httpx` async client | AniList GraphQL, Nyaa bytes, Transmission RPC |
| HTML parsing | `beautifulsoup4` + `lxml` | Direct equivalent of html-conduit |
| XML/RSS parsing | `defusedxml` | Safe XML; equivalent of xml-conduit |
| Fuzzy title match | `rapidfuzz` | Release title vs AniList names in the matcher |
| Torrent client | Transmission JSON-RPC | Lightweight, no extra binary |
| Transmission start/stop | `systemctl` on a configurable unit | The UI controls the daemon's lifetime |
| Jellyfin | `POST /Items/{id}/Refresh` on the show, `SCAN_DELAY` after Transmission finishes; no follow-up check | Same scan as Jellyfin's own UI, scoped to one show; the delay lets an rclone mount upload the file first |
| Watchlist format | TOML (`anime.toml`) | Human-editable; holds configuration only |
| Runtime state | JSON next to the watchlist | History and cache kept out of the hand-edited file |
| Web framework | FastAPI | Async-native, minimal |
| Frontend | Static HTML/CSS/ES modules | No build step; `uv sync` is the whole install |

---

## Watchlist format

See `anime.example.toml` and the README's table. Fields: `title`, `type`, `anilist_id`, `submitters`, `search_name`, `download_dir`, `check_delay`, `episode_offset`, `last_chapter`, `deprecated`. Read with stdlib `tomllib`; written with a minimal serialiser in `file_manager._write` (unknown keys and comments are not preserved).

`title` is the entry's key everywhere (API bodies, `TrackerState`). `anilist_id` is resolved once by title and then stored.

---

## Environment variables

Documented in the README and `.env.example`. All are read in `config.load_config()`; add new ones there and to both documents.

---

## Running

```bash
uv sync
uv run lydarr                  # web UI + tracker (tracker resumes if it was on)
uv run lydarr --no-web         # tracker only
uv run lydarr check [title]    # dry run: what would be picked, and why
uv run pytest
```

A live instance may be running from the main checkout: its static files are served straight from disk, so develop UI changes in a worktree and test on another port.

---

## Conventions

- **snake_case** everywhere — functions, variables, modules
- Keyword arguments and defaults are written with spaces: `f(key = value)`, `field(default_factory = list)`
- **No comments** unless the why is non-obvious (a hidden constraint, workaround, etc.)
- **Dataclasses** for all record types; `frozen=True` where the object shouldn't mutate
- **Custom exceptions** (`NyaaError`, `AnilistError`) for domain errors; the tracker and the app's exception handlers are the boundaries that catch them
- **`asyncio.sleep`** instead of `time.sleep` — all loops are async. Code that tests need to fast-forward calls a module-level `_pause()`; tests patch that, never `asyncio.sleep` itself (patching it globally turns the app's background loops into busy loops)
- Prefer returning `None` over raising when a "not found" is a normal outcome (e.g., no torrent yet)
- Tests must not reach the network; `tests/conftest.py` enforces it
- Frontend: no inline scripts or handlers (the CSP forbids them); panels talk through the event bus in `js/state.js`

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
