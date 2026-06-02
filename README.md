# That Time Crunchyroll Ruined My Life, So I Paid Claude $100 To Revive Me?

"That Time Crunchyroll Ruined My Life, So I Paid Claude $100 To Revive Me?" (クランチロールに人生をぶっ壊された　クロードに１００００円を払っちゃってオレは復活した件）

Anime torrent downloader daemon that tracks airing anime/manga. The daemon waits for each episode or chapter's release, searches nyaa, and adds them to `transmission-daemon` automatically. A web UI is also included for QOL to search for and manage anime/manga.

## Requirements

1. Install `uv`.

2. `apt install transmission-daemon`

## Setup

```bash
uv sync
```

Add entries to `anime.toml` manually:

```toml
[[media]]
title = "TITLE_OF_ANIME"
type = "anime" # "anime" or "manga"
search_name = "SEARCH_NAME" # title used to search nyaa with
submitters = ["SUBMITTER_1", "SUBMITTER_2"] # chosen submitter(s)
deprecated = true # skipped by daemon


# TODO
[[media]]
title = "TITLE_OF_MANGA"
type = "manga"
submitters = [] # empty = accept all uploaders
last_chapter = 1
```

Or use the web-ui below...

## Configuration

Copy the example env file and fill in your values:

Either locally:

```bash
# Daemon only
uv run python -m lydarr

# Daemon + web UI
LYDARR_WEB=1 uv run python -m lydarr

# Web UI only (browse and manage without auto-tracking)
uv run python -m lydarr --web-only

# Bind to a specific address/port (e.g. behind a reverse proxy)
uv run python -m lydarr --web-only --host 127.0.0.1 --port 8080

# Write logs to a file in addition to stdout
uv run python -m lydarr --log lydarr.log
```

The web UI is served at `http://localhost:8080` by default.

## How it works

For each entry in `anime.toml`, a coroutine runs concurrently via `asyncio.gather`:

**Anime:**
1. Fetches episode air schedule from AniList
2. Sleeps until the next episode's air time
3. Searches Nyaa.si (tries four query variants, filters by submitter, prefers HEVC/x265)
4. Retries every 30 minutes until a torrent is found
5. Adds the magnet to Transmission via its JSON-RPC API, saving to a per-title subfolder under `LYDARR_DEFAULT_DIR`
6. Removes the entry from `anime.toml` once all episodes are downloaded

**Manga:**
1. Fetches chapter count and status from AniList
2. Downloads any chapters newer than `last_chapter` sequentially
3. Updates `last_chapter` in `anime.toml` after each successful add
4. Checks again every 24 hours (releasing/hiatus) or removes the entry when finished

## Web UI

Three resizable panels:

- **Media search** — search AniList by title (anime or manga), view status and next airing info, add to watchlist
- **Watchlist** — current `anime.toml` entries with submitter tags; click a title to search its torrents; edit submitters and search name inline; deprecate or remove entries
- **Torrent browser** — search Nyaa by title and optional episode number, filter by HEVC/x265 or torrent type (Trusted/Normal/Remake), sort by any column (resizable), add directly to Transmission

When adding a torrent from the browser, click the show title in the watchlist panel first to set the download folder context — the torrent will be saved to the correct per-title subfolder under `LYDARR_DEFAULT_DIR`. Clearing the search box (✕) resets the context.

**Header** — red/green status dots for Transmission and Daemon. **Floating button** (bottom-right) starts and stops both the lydarr daemon and Transmission together via `systemctl --user`.

## Running as a systemd service

### Transmission

Both lydarr and Transmission must run as the **same user** so Transmission can write to that user's download directory. The system package unit (`/lib/systemd/system/transmission-daemon.service`) runs as `debian-transmission`, so a user-level unit is required instead:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/transmission-daemon.service << 'EOF'
[Unit]
Description=Transmission BitTorrent Daemon
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/transmission-daemon -f --log-level=error
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now transmission-daemon
```

#### rclone / FUSE download targets

If `LYDARR_DEFAULT_DIR` is on an rclone FUSE mount, disable partial-file renaming in Transmission's `~/.config/transmission-daemon/settings.json` (stop Transmission first, edit, then restart):

```json
"rename-partial-files": false
```

Without this, Transmission downloads to `filename.mkv.part` and renames on completion — rclone FUSE does not support that rename and the transfer fails with an I/O error.

### Lydarr

A service template is included at `lydarr.service`. It assumes the repo is at `~/git/lydarr-py`; adjust the paths if needed:

```bash
# Install
mkdir -p ~/.config/systemd/user
cp lydarr.service ~/.config/systemd/user/lydarr.service
systemctl --user daemon-reload
systemctl --user enable --now lydarr

# Survive logout (keeps user services running without an active login session)
# Required for both lydarr and transmission-daemon user services
loginctl enable-linger
```

Environment variables go in `~/.config/lydarr/env` (one `KEY=value` per line, no `export`). Use absolute paths — the working directory when running as a service may differ from the repo root:

```bash
mkdir -p ~/.config/lydarr
cp .env.example ~/.config/lydarr/env
```

In `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `ANIME_FILE` | `anime.toml` | Path to watchlist file (use absolute path when running as a service) |
| `TRANSMISSION_URL` | `http://localhost:9091/transmission/rpc` | Transmission RPC endpoint |
| `TRANSMISSION_USER` | unset | Transmission username (if auth enabled) |
| `TRANSMISSION_PASS` | unset | Transmission password (if auth enabled) |
| `LYDARR_DEFAULT_DIR` | `~/Downloads` | Root download directory; each title gets its own subfolder |
| `LYDARR_USER` | unset | Web UI username for HTTP Basic Auth; leave unset to disable auth |
| `LYDARR_PASS` | unset | Web UI password; required when `LYDARR_USER` is set |
| `LYDARR_WEB` | unset | Set to `1` to enable web UI alongside daemon |
| `LYDARR_WEB_HOST` | `0.0.0.0` | Web UI bind address (overridden by `--host`) |
| `LYDARR_WEB_PORT` | `8080` | Web UI port (overridden by `--port`) |

## Usage

```bash
# Daemon only
uv run python -m lydarr

# Web UI only (daemon can be started on web)
uv run python -m lydarr --web-only

# Bind to a specific address/port
uv run python -m lydarr --web-only --host 127.0.0.1 --port 8080
```

## Systemd service

Template is included at `lydarr.service`.

```bash
mkdir -p ~/.config/systemd/user
cp lydarr.service ~/.config/systemd/user/lydarr.service
systemctl --user daemon-reload
systemctl --user enable --now lydarr

loginctl enable-linger
```
