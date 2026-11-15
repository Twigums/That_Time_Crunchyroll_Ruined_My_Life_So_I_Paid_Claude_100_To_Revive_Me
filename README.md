# That Time Crunchyroll Ruined My Life, So I Paid Claude $100 To Revive Me?

"That Time Crunchyroll Ruined My Life, So I Paid Claude $100 To Revive Me?" (クランチロールに人生をぶっ壊された　クロードに１００００円を払っちゃってオレは復活した件）


## Quick start

```bash
cp lydarr.service ~/.config/systemd/user/lydarr.service
systemctl --user daemon-reload
systemctl --user enable --now lydarr
loginctl enable-linger
```

## Using the web UI

On a wide screen there are three panels; on a phone a bar at the bottom switches between them.

**Header.** Two pills show whether the tracker and Transmission are running; click one to start or stop just that. **Start** brings up Transmission and then the tracker; **Stop** stops the tracker and then Transmission. The refresh button re-reads AniList, and the text beside it says how old the data is.

**Add a show.** Search AniList and press **Track**. For a show that is already airing, **+ aired eps** also queues the episodes that are already out; plain **Track** only follows what airs from now on.

**Watchlist.** Each show shows its next episode, or what the tracker is doing about the current one. Click the title to look for its torrents. The **⋯** menu has:

- **Edit settings**
  - *Search name on Nyaa*: what to search for when the AniList title finds nothing (most groups use the romaji title).
  - *Preferred uploaders*: best first. Blank accepts anyone.
  - *Save path*: the full folder for this show. Blank uses `LYDARR_DEFAULT_DIR/<AniList title>`, shown as the placeholder.
  - *Check delay*: minutes after air time before the first search.
  - *Episode number offset*: see [below](#when-it-picks-wrong-or-picks-nothing).
- **Download aired episodes**: queue everything that aired before you added the show.
- **Archive**: stop tracking but keep the entry and its settings. A show that finishes airing is archived for you once its last episode is in.
- **Remove**: delete the entry and its history.

**Calendar.** Every tracked show's episodes as a list, a week or a month, in your local time. Click an episode to see its releases.

**Torrents.** Search Nyaa and add a release by hand.

- **Save to** decides where an added torrent goes. Clicking a show or a calendar episode sets it to that show, and the path underneath is exactly where the files will land. It stays set while you edit the search box. Set it to *A new folder named after the release* to file something that is not on your watchlist.
- Each result shows the release's group, episode number, resolution and codec, read from its name.
- When you arrive from the calendar (a show and an episode number), every result is labelled with what the tracker makes of it: **Match**, waiting for a preferred uploader or an HEVC encode, a number that needs checking, or the reason it was ruled out. Results for other episodes are hidden until you ask for them.
- Adding a release for a tracked show marks that episode as done, so the tracker will not fetch it again.

**Activity.** Every torrent that was added, when, and where it was saved. At the top are releases the tracker **held for your check**: the right show at the right time, but with an episode number it did not expect. **Add this** downloads it and teaches the tracker that numbering, so the following weeks need no help.

## How an episode is chosen

AniList says when each episode airs. After the check delay, lydarr asks Nyaa for the newest uploads matching the show and reads every release name. A release is only used if all of these hold:

1. **It was uploaded after the episode aired** (within a day before, to allow for early streams). This is what stops last season's episode 4 from being taken for this season's.
2. **It is the same series.** The title has to match one of the show's AniList names, or contain every word of your search name.
3. **It is one regular episode**: not a batch, recap, special, OVA or creditless opening.
4. **Its number fits.** Either the number AniList uses, or that number plus the length of the earlier seasons. Season 3 episode 4 of a show whose first two seasons ran 24 episodes each is accepted as `- 04`, `3rd Season - 04`, `S03E04` and `- 52`. A release labelled with a different season is not accepted.

Among the releases that pass:

- Your **preferred uploaders** come first. If none of them has released within `LYDARR_FALLBACK_HOURS` (default 2) of the first check, anyone's release is accepted.
- **HEVC** is preferred. If only a non-HEVC release is out, lydarr holds on for up to `LYDARR_HEVC_WAIT_HOURS` (default 2) for an HEVC one, then takes what there is.
- Then the preferred resolution (`LYDARR_RESOLUTION`, default 1080), a `v2` over the original, trusted uploads, and finally seeders.

Searches repeat every 15 minutes at first, then less often, for 14 days. A release that never appears does not hold up later episodes, and episodes that aired while lydarr was stopped are picked up when it starts again.

### When it picks wrong, or picks nothing

- **Nothing is found.** Usually the group uses a different title. Set *Search name on Nyaa* to the title you see on Nyaa.
- **It is held for your check.** Open **Activity** and press **Add this** on the right release. The numbering is remembered for that show.
- **You know the numbering already.** Set *Episode number offset* to the difference: if the group calls season 3 episode 4 `- 52`, the offset is 48.
- **It resolved to the wrong AniList entry.** Remove the show and add it again from the search results, which stores the exact AniList id.
- **You want to see its reasoning.** `uv run lydarr check "title"` prints every candidate with its verdict, without downloading anything.

Manga tracking is **experimental**. AniList only reports a chapter count once a series has ended, so ongoing manga are not picked up automatically.

## Configuration

### Environment variables

Set these in `.env` (see `.env.example`) or in the environment.

| Variable | Default | Purpose |
|---|---|---|
| `ANIME_FILE` | `anime.toml` | The watchlist file |
| `LYDARR_DEFAULT_DIR` | `~/Downloads` | Root download folder; each show gets a subfolder unless it sets its own save path |
| `TRANSMISSION_URL` | `http://localhost:9091/transmission/rpc` | Transmission RPC endpoint |
| `TRANSMISSION_USER` / `TRANSMISSION_PASS` | unset | Transmission credentials, if it requires them |
| `LYDARR_TRANSMISSION_UNIT` | `transmission-daemon` | systemd unit the Start/Stop controls act on |
| `LYDARR_TRANSMISSION_SCOPE` | `user` | `user` runs `systemctl --user`; `system` runs `sudo -n systemctl` |
| `LYDARR_FALLBACK_HOURS` | `2` | Hours to wait for preferred uploaders before accepting others |
| `LYDARR_PREFER_HEVC` | `1` | Prefer HEVC/x265; `0` takes the best release straight away |
| `LYDARR_HEVC_WAIT_HOURS` | `2` | Hours to hold out for an HEVC release |
| `LYDARR_RESOLUTION` | `1080` | Preferred vertical resolution |
| `LYDARR_WEB_HOST` | `0.0.0.0` | Web UI bind address (`--host` overrides) |
| `LYDARR_WEB_PORT` | `8080` | Web UI port (`--port` overrides) |
| `LYDARR_USER` / `LYDARR_PASS` | unset | HTTP Basic Auth for the web UI |
| `LYDARR_LOG_FILE` | unset | Also log to this file (`--log` overrides) |
| `LYDARR_STATE_FILE` | next to the watchlist | Download history and tracker state |
| `LYDARR_CACHE_FILE` | next to the watchlist | AniList cache |
| `JELLYFIN_URL` | unset | Jellyfin server, e.g. `http://jellyfin:8096`; unset turns the [Jellyfin](#jellyfin) step off |
| `JELLYFIN_API_KEY` | unset | API key from Jellyfin's *Dashboard → API Keys* |
| `JELLYFIN_PATH_MAP` | detected | `lydarr path=Jellyfin path`, several separated by `;`, when Jellyfin sees the downloads elsewhere |

Without `LYDARR_USER`, anyone who can reach the page can add torrents and change save paths. Set a password, or bind to `127.0.0.1`, unless the network is one you trust.

### The watchlist file

The web UI writes `anime.toml` for you; `anime.example.toml` shows every field. Editing by hand works too, but restart lydarr afterwards, and note that comments are not kept when the UI next saves.

```toml
[[media]]
title = "The Apothecary Diaries Season 3"
type = "anime"
anilist_id = 195516
submitters = ["SubsPlease", "Erai-raws"]
search_name = "Kusuriya no Hitorigoto"
download_dir = "/media/Anime/Apothecary Diaries/Season 3"
check_delay = 145
```

| Field | Meaning |
|---|---|
| `title` | Display name, and what AniList is searched for the first time |
| `type` | `anime` (default) or `manga` |
| `anilist_id` | Filled in on first lookup; pins the entry to one AniList page |
| `submitters` | Preferred uploaders, best first; empty accepts anyone |
| `search_name` | Nyaa search text, when the title finds nothing |
| `download_dir` | Full save path for this show; empty uses `LYDARR_DEFAULT_DIR/<AniList title>` |
| `check_delay` | Minutes after air time before the first search (default 60) |
| `episode_offset` | Release number minus AniList number; leave out to detect |
| `last_chapter` | Manga only: the last chapter you have |
| `deprecated` | `true` for an archived entry |

### Files lydarr writes

| File | Contents | Safe to delete? |
|---|---|---|
| `anime.toml` | Your watchlist | No |
| `lydarr-state.json` | Which episodes were downloaded, the activity log, learned numbering, whether the tracker is on | Yes, but history is lost and tracking restarts from the next episode to air |
| `lydarr-cache.json` | AniList data | Yes; it is fetched again |

### AniList usage

AniList rate-limits clients, sharply so when its servers are struggling. lydarr asks for everything it tracks in a few combined requests every six hours and keeps the answers on disk, so page loads, the calendar and the tracker cost no requests at all. If AniList is down or says to slow down, lydarr keeps working from what it has, shows the age of the data in the header, and tries again later with increasing gaps.

## Transmission setup

lydarr and Transmission must run as the **same user**, so Transmission can write to that user's folders. The packaged unit runs as `debian-transmission`, so use a user unit instead:

```bash
sudo systemctl disable --now transmission-daemon

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

Finished torrents are removed from Transmission's list once complete; the files stay where they were saved.

**rclone or other FUSE download folders.** Turn off partial-file renaming in `~/.config/transmission-daemon/settings.json` (stop Transmission, edit, start it again):

```json
"rename-partial-files": false
```

Otherwise Transmission downloads to `name.mkv.part` and renames at the end, which rclone's FUSE layer does not support, and the transfer fails with an I/O error.

## Jellyfin

When Transmission finishes a download, lydarr can ask Jellyfin to scan the show it belongs to, so the episode appears without waiting for Jellyfin's next library scan. Create an API key under *Dashboard → API Keys* in Jellyfin, then set:

```bash
JELLYFIN_URL=http://jellyfin:8096
JELLYFIN_API_KEY=0123456789abcdef0123456789abcdef
```

Two minutes after a download finishes, lydarr finds the Jellyfin show whose folder holds the file and asks Jellyfin to scan it, the same as *Scan for new and updated files* in Jellyfin's own menu. A show Jellyfin does not list yet gets its whole library scanned instead. The two minutes give an rclone mount time to upload the file, which it only starts once Transmission has closed it. lydarr sends the request once and does not check the result; if Jellyfin cannot be reached, the log says so and Jellyfin's next library scan picks the file up.

**Paths.** Jellyfin often sees the same files under another folder: `/home/me/media/Jellyfin/Anime` on this machine might be `/media/Jellyfin/Anime` inside Jellyfin's container. lydarr lines the two up by folder names, so a library folder ending in `Jellyfin/Anime` matches the `Jellyfin/Anime` part of the download path. When the names differ, or two libraries match equally well, give the mapping as `lydarr path=Jellyfin path`, separating several pairs with `;`:

```bash
JELLYFIN_PATH_MAP=/home/me/media/Jellyfin=/media/Jellyfin
```

`uv run lydarr check "title"` lists Jellyfin's library folders and, for each show, the Jellyfin show and folder its downloads land in. It never asks Jellyfin to scan.

## Troubleshooting

| Symptom | What to check |
|---|---|
| A show says **Not found on AniList** | The title did not match. Remove it and add it from the search results. |
| A show says **Looking for ep N** for hours | Run `uv run lydarr check "title"`. No candidates at all means the search name is wrong; candidates marked as waiting are within the uploader or HEVC wait. |
| The header says **AniList unavailable** | AniList is down or rate limiting. Nothing to do; cached data is used and it retries by itself. |
| An episode landed in the wrong folder | Check the show's *Save path*, and that **Save to** named that show when you added a torrent by hand. The Activity log records where each torrent went. |
| **Transmission did not accept the torrent** | Transmission is not running, or the save path is not writable by its user. |
| A new episode does not show up in Jellyfin | Check the log. *No library holds…* means the download path did not line up with a Jellyfin library folder: set `JELLYFIN_PATH_MAP` (`uv run lydarr check` lists Jellyfin's folders). *Asked Jellyfin to scan…* means the request went through: check that Jellyfin can read the folder and recognises the file name. |
| The Start button reports a systemctl error | `LYDARR_TRANSMISSION_UNIT` / `LYDARR_TRANSMISSION_SCOPE` do not match how Transmission is installed. |
| The service will not start | `journalctl --user -u lydarr -n 50`. A wrong path in the unit file is the usual cause. |

## Upgrading from an older checkout

- **Your watchlist carries over.** On first start each entry is matched to AniList once and `anilist_id` is written into `anime.toml`. This takes a minute or two for a long list.
- **Tracking starts from the next episode to air.** Nothing that aired before the upgrade is fetched unless you choose **Download aired episodes**.
- **The tracker starts off.** Press **Start** once; it is remembered from then on.
- `uv run lydarr` now serves the web UI and runs the tracker together. `LYDARR_WEB=1` is no longer needed, `--web-only` is accepted and means the same as no flag, and `--debug` became `lydarr check`.
- A finished show is archived instead of being re-downloaded and removed.

## Development

```bash
uv sync
uv run pytest              # the suite never touches the network
```

```
modules/anilist/     AniList GraphQL client: rate-limit gate, batched lookups
modules/nyaa/        Nyaa scraping and search (port of nyaa-hs)
src/lydarr/
  release.py         release-name parser
  matcher.py         decides whether a torrent is a given episode
  media_store.py     disk-backed AniList cache shared by everything
  state.py           download history, activity log, tracker state
  tracker.py         the episode queue
  jellyfin.py        asks Jellyfin to scan finished downloads in
  web/               FastAPI app, routes and the static UI
```

`CLAUDE.md` has the longer tour.
