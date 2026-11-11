"""Episode scheduler: works out what is due, finds it on Nyaa and hands it to Transmission.

One loop serves every show. Each tick it reads the cached AniList schedule, picks
the episodes that have aired (plus their check delay) and are not settled yet,
and checks each on its own retry timer. A release that never appears therefore
cannot hold up later episodes, and anything that aired while lydarr was stopped
is picked up on the next start.
"""
import asyncio
import logging
import os
import time

from anilist.types import AnilistError, AnilistMedia, MediaStatus
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry, MediaState
from lydarr.matcher import (
    EARLY_TOLERANCE, Candidate, MatchResult, Policy, Target,
    accepted_numbers, evaluate, expected_seasons, fits, group_key, in_episode_window,
)
from lydarr.media_store import MediaStore
from lydarr.nyaa_search import clean_query, find_candidates, search_chapter
from lydarr.paths import resolve_download_dir, safe_dirname
from lydarr.release import Release, parse_release, split_season
from lydarr.state import GRABBED, MISSED, PENDING, REVIEW, SETTLED, SKIPPED, EpisodeRecord, ShowState, TrackerState
from lydarr.torrent_client import AddedTorrent, add_magnet, remove_torrents, torrent_progress
from nyaa.parser._utils import magnet_hash
from nyaa.types import NyaaError, Torrent

_logger = logging.getLogger("lydarr.tracker")

DEFAULT_CHECK_DELAY = 60  # minutes after air time before searching Nyaa
TICK_SECONDS = 60
REAP_SECONDS = 30
GIVE_UP_AFTER = 14 * 86400
MAX_CHECKS_PER_TICK = 6
REVIEW_KEEP = 6
ADD_RETRY = 300
NYAA_PAUSE = 1.0
MANGA_POLL = 24 * 3600
MANGA_RETRY = 30 * 60


def _log(name: str, msg: str) -> None:
    _logger.info("[%s] %s", name, msg)


def _pad(n: int) -> str:
    return f"{n:02d}"


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


def retry_interval(elapsed: float) -> int:
    """Seconds until the next search, given how long an episode has been looked for."""
    if elapsed < 2 * 3600:
        return 15 * 60
    if elapsed < 12 * 3600:
        return 30 * 60
    if elapsed < 48 * 3600:
        return 3600
    return 6 * 3600


def candidate_dict(c: Candidate) -> dict:
    t, r = c.torrent, c.release
    return {
        "hash": t.info_hash,
        "name": t.name,
        "magnet": t.magnet,
        "group": r.group,
        "season": r.season,
        "episode": r.episode,
        "resolution": r.resolution,
        "hevc": r.hevc,
        "published": t.published,
        "seeders": t.seeders,
        "size": t.size,
        "listed": c.listed,
        "offset": c.offset,
        "verdict": c.verdict,
        "reason": c.reason,
    }


class Tracker:
    def __init__(self, cfg: AppConfig, media_state: MediaState, store: MediaStore, tstate: TrackerState) -> None:
        self.cfg = cfg
        self.media_state = media_state
        self.store = store
        self.tstate = tstate
        self._manga_tasks: dict[str, asyncio.Task] = {}
        self._listings: dict[str, list[Torrent]] = {}
        self._nyaa_calls = 0
        self._reap_failing = False

    @property
    def policy(self) -> Policy:
        return Policy(
            fallback_after = self.cfg.fallback_hours * 3600,
            hevc_wait = self.cfg.hevc_wait_hours * 3600,
            prefer_hevc = self.cfg.prefer_hevc,
            resolution = self.cfg.resolution,
        )

    # ── loops ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        active = [e for e in self.media_state.entries() if not e.deprecated]
        _logger.info("Tracker started with %d active title(s).", len(active))
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    _logger.exception("Tracker pass failed; continuing.")
                await asyncio.sleep(TICK_SECONDS)
        finally:
            for task in self._manga_tasks.values():
                task.cancel()
            self._manga_tasks.clear()
            _logger.info("Tracker stopped.")

    async def housekeeping(self) -> None:
        """Runs whether or not the tracker does, so hand-added torrents are cleaned up too."""
        while True:
            await asyncio.sleep(REAP_SECONDS)
            try:
                await self.reap()
            except Exception:
                _logger.exception("Housekeeping pass failed; continuing.")

    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.store.refresh_soon()
        self._listings = {}
        self._nyaa_calls = 0
        self._sync_manga_tasks()
        budget = MAX_CHECKS_PER_TICK
        changed = False
        for entry in self.media_state.entries():
            if entry.deprecated or entry.media_type == "manga":
                continue
            used, touched = await self._process(entry, now, budget)
            budget -= used
            changed = changed or touched
        if changed:
            await self.tstate.save()

    # ── anime ─────────────────────────────────────────────────────────────

    def _schedule(self, entry: MediaEntry, info: AnilistMedia, show: ShowState) -> list[tuple[int, int | None]]:
        rows: list[tuple[int, int | None]] = list(self.store.episodes(entry))
        dateless = (
            show.backfill and info.status == MediaStatus.FINISHED and info.episodes
            and self.store.has_full_schedule(entry) and self.store.prequels(entry) is not None
        )
        if dateless:
            known = {episode for episode, _ in rows}
            rows.extend((episode, None) for episode in range(1, (info.episodes or 0) + 1) if episode not in known)
            rows.sort(key = lambda row: row[0])
        return rows

    async def _process(self, entry: MediaEntry, now: float, budget: int) -> tuple[int, bool]:
        info = self.store.info(entry)
        if info is None:
            return 0, False
        changed = self.tstate.peek(entry.title) is None
        show = self.tstate.show(entry.title, now)
        delay = (entry.check_delay or DEFAULT_CHECK_DELAY) * 60
        schedule = self._schedule(entry, info, show)
        used = 0

        for index, (episode, airing_at) in enumerate(schedule):
            rec = show.episodes.get(episode)
            if airing_at is not None:
                if airing_at + delay > now:
                    if rec is not None and rec.status in (PENDING, REVIEW) and rec.first_check:
                        # Rescheduled after we started looking: start over when it really airs.
                        del show.episodes[episode]
                        changed = True
                    continue
                if not show.backfill and airing_at < show.since:
                    continue
            if rec is not None and rec.status in SETTLED:
                continue
            if rec is None:
                rec = self.tstate.episode(entry.title, episode)
            if not rec.first_check:
                rec.first_check = now
                changed = True
            if now - rec.first_check > GIVE_UP_AFTER:
                rec.status = MISSED
                rec.at = now
                rec.candidates = []
                self.tstate.log("missed", entry.title, episode, detail = rec.note, now = now)
                _log(entry.title, f"Ep {_pad(episode)}: no usable release after {GIVE_UP_AFTER // 86400} days; giving up.")
                changed = True
                continue
            if now < rec.next_check or used >= budget:
                continue
            next_airing = schedule[index + 1][1] if index + 1 < len(schedule) else None
            await self._check(entry, info, show, episode, airing_at, next_airing, rec, now)
            used += 1
            changed = True

        if await self._maybe_complete(entry, info, show, schedule, now):
            changed = True
        return used, changed

    def _target(self, entry: MediaEntry, info: AnilistMedia | None, show: ShowState, episode: int,
                airing_at: int | None, next_airing_at: int | None) -> Target:
        names = tuple(dict.fromkeys([*(info.titles() if info else []), entry.title]))
        prequels = self.store.prequels(entry)
        delay = (entry.check_delay or DEFAULT_CHECK_DELAY) * 60
        return Target(
            episode = episode,
            airing_at = airing_at,
            next_airing_at = next_airing_at,
            due_at = airing_at + delay if airing_at is not None else None,
            names = names,
            search_name = entry.search_name,
            seasons = expected_seasons(names, prequels),
            prequel_counts = tuple(prequels or ()),
            offset_override = entry.episode_offset,
            learned = dict(show.offsets),
            extra_offsets = frozenset(show.extra_offsets),
            submitters = tuple(entry.submitters),
            require_season_proof = airing_at is None and bool(prequels),
        )

    def queries(self, entry: MediaEntry) -> list[str]:
        """The Nyaa queries used to find this entry's releases."""
        return self._queries(entry, self.store.info(entry))

    def _queries(self, entry: MediaEntry, info: AnilistMedia | None) -> list[str]:
        if entry.search_name:
            return [entry.search_name]
        names = [info.title_romaji if info else None, entry.title, info.title_english if info else None]
        out: list[str] = []
        for name in names:
            query = clean_query(split_season(name)[0]) if name else ""
            if query and query.lower() not in (q.lower() for q in out):
                out.append(query)
        return out[:2]

    async def _search(self, entry: MediaEntry, info: AnilistMedia | None, target: Target, now: float,
                      query: str | None = None, listings: dict[str, list[Torrent]] | None = None) -> MatchResult:
        listings = self._listings if listings is None else listings
        found: dict[str, Torrent] = {}
        for q in ([query] if query else self._queries(entry, info)):
            if q not in listings and self._nyaa_calls:
                await _pause(NYAA_PAUSE)
            self._nyaa_calls += 1
            for t in await find_candidates(q, accepted_numbers(target), target.airing_at, listings):
                found.setdefault(t.info_hash or t.id, t)
        return evaluate(target, list(found.values()), now, self.policy)

    def _note(self, title: str, episode: int, rec: EpisodeRecord, note: str) -> None:
        if note != rec.note:
            rec.note = note
            _log(title, f"Ep {_pad(episode)}: {note}.")

    async def _check(self, entry: MediaEntry, info: AnilistMedia, show: ShowState, episode: int,
                     airing_at: int | None, next_airing_at: int | None, rec: EpisodeRecord, now: float) -> None:
        target = self._target(entry, info, show, episode, airing_at, next_airing_at)
        rec.checks += 1
        rec.next_check = now + retry_interval(now - rec.first_check)
        try:
            result = await self._search(entry, info, target, now)
        except NyaaError as exc:
            self._note(entry.title, episode, rec, f"Nyaa search failed ({exc})")
            return

        if result.best is not None:
            await self._grab(entry, info, show, episode, rec, candidate_dict(result.best), now,
                             kind = "grabbed", group_offset = result.best.offset)
            return

        fallback_passed = target.due_at is None or now >= target.due_at + self.policy.fallback_after
        if result.near and not result.waiting and fallback_passed:
            rec.candidates = [candidate_dict(c) for c in result.near[:REVIEW_KEEP]]
            if rec.status != REVIEW:
                rec.status = REVIEW
                rec.at = now
                self.tstate.log("review", entry.title, episode, detail = result.near[0].reason, now = now)
            self._note(entry.title, episode, rec, f"held for review ({result.near[0].reason})")
        else:
            if rec.status == REVIEW:
                rec.status = PENDING
                rec.candidates = []
            self._note(entry.title, episode, rec, result.summary())

    async def _send(self, magnet: str, download_dir: str) -> tuple[AddedTorrent | None, str]:
        try:
            os.makedirs(download_dir, exist_ok = True)
        except OSError as exc:
            return None, f"cannot create {download_dir} ({exc.strerror or exc})"
        added = await add_magnet(
            self.cfg.transmission_url, self.cfg.transmission_user, self.cfg.transmission_pass,
            magnet, download_dir,
        )
        if added is None:
            return None, "Transmission did not accept the torrent"
        return added, ""

    async def _grab(self, entry: MediaEntry, info: AnilistMedia | None, show: ShowState, episode: int,
                    rec: EpisodeRecord, cand: dict, now: float, *, kind: str,
                    group_offset: int | None = None, show_offset: int | None = None) -> tuple[bool, str]:
        download_dir = resolve_download_dir(self.cfg, entry, info)
        added, error = await self._send(cand["magnet"], download_dir)
        if added is None:
            self._note(entry.title, episode, rec, f"{error}; will retry")
            rec.next_check = now + ADD_RETRY
            return False, error

        info_hash = added.hash or cand.get("hash") or magnet_hash(cand["magnet"])
        rec.status = GRABBED
        rec.hash = info_hash
        rec.name = cand["name"]
        rec.at = now
        rec.note = ""
        rec.candidates = []
        group = group_key(cand.get("group") or "")
        learned = show_offset if show_offset is not None else group_offset
        if group and learned is not None:
            show.offsets[group] = learned
        if show_offset is not None and show_offset not in show.extra_offsets:
            show.extra_offsets.append(show_offset)
        self.tstate.watch(info_hash)
        self.tstate.log(kind, entry.title, episode, name = cand["name"], info_hash = info_hash,
                        directory = download_dir, detail = cand.get("reason", ""), now = now)
        _log(entry.title, f"Ep {_pad(episode)}: added {cand['name']} -> {download_dir}")
        return True, ""

    async def _maybe_complete(self, entry: MediaEntry, info: AnilistMedia, show: ShowState,
                              schedule: list[tuple[int, int | None]], now: float) -> bool:
        if info.status != MediaStatus.FINISHED or show.completed_at:
            return False
        for episode, airing_at in schedule:
            if airing_at is not None and airing_at > now:
                return False
            if not (show.backfill or airing_at is None or airing_at >= show.since):
                continue
            rec = show.episodes.get(episode)
            if rec is None or rec.status not in SETTLED:
                return False
        if not any(rec.status in SETTLED for rec in show.episodes.values()):
            return False
        show.completed_at = now
        await self.media_state.set_deprecated(self.cfg.anime_file, entry.title, True)
        self.tstate.log("completed", entry.title, detail = "finished airing", now = now)
        _log(entry.title, "Finished airing and every tracked episode is settled; archived.")
        return True

    # ── manga (experimental) ──────────────────────────────────────────────

    def _sync_manga_tasks(self) -> None:
        wanted = {e.title for e in self.media_state.entries() if e.media_type == "manga" and not e.deprecated}
        for title in list(self._manga_tasks):
            task = self._manga_tasks[title]
            if title not in wanted:
                task.cancel()
            if title not in wanted or task.done():
                del self._manga_tasks[title]
        for title in wanted:
            if title not in self._manga_tasks:
                self._manga_tasks[title] = asyncio.create_task(self._track_manga(title))

    async def _track_manga(self, title: str) -> None:
        while True:
            entry = self.media_state.get(title)
            if entry is None or entry.deprecated:
                return
            try:
                pause = await self._step_manga(entry)
            except Exception as exc:
                _log(title, f"Manga check failed ({exc!r}). Retrying in 1h.")
                pause = 3600
            if pause is None:
                return
            await asyncio.sleep(pause)

    async def _step_manga(self, entry: MediaEntry) -> float | None:
        """One pass over a manga entry; returns seconds to wait, or None when it is finished."""
        info = self.store.info(entry)
        if info is None:
            return 3600
        # AniList only fills in `chapters` once a series has ended, so an ongoing
        # manga reports no new chapters here; that is why this path is experimental.
        total = info.chapters or 0
        download_dir = resolve_download_dir(self.cfg, entry, info)
        for chapter in range(entry.last_chapter + 1, total + 1):
            torrent = await search_chapter(entry.search_name or entry.title, chapter, entry.submitters)
            if torrent is None:
                _log(entry.title, f"No scanlation for ch.{chapter:03d} yet.")
                return MANGA_RETRY
            added, error = await self._send(torrent.magnet, download_dir)
            if added is None:
                _log(entry.title, f"ch.{chapter:03d}: {error}; will retry.")
                return ADD_RETRY
            info_hash = added.hash or torrent.info_hash
            self.tstate.watch(info_hash)
            self.tstate.log("grabbed", entry.title, name = torrent.name, info_hash = info_hash,
                            directory = download_dir, detail = f"ch.{chapter:03d}")
            await self.tstate.save()
            await self.media_state.update_last_chapter(self.cfg.anime_file, entry.title, chapter)
            _log(entry.title, f"ch.{chapter:03d}: added {torrent.name} -> {download_dir}")
        if info.status == MediaStatus.FINISHED and total > 0:
            await self.media_state.set_deprecated(self.cfg.anime_file, entry.title, True)
            self.tstate.log("completed", entry.title, detail = "finished")
            await self.tstate.save()
            _log(entry.title, "Finished; archived.")
            return None
        return MANGA_POLL

    # ── finished torrents ─────────────────────────────────────────────────

    async def reap(self) -> None:
        """Drop completed torrents from Transmission's list (the files stay)."""
        hashes = self.tstate.reap_hashes()
        if not hashes:
            return
        url, user, password = self.cfg.transmission_url, self.cfg.transmission_user, self.cfg.transmission_pass
        progress = await torrent_progress(url, user, password, hashes)
        if progress is None:
            if not self._reap_failing:
                self._reap_failing = True
                _logger.info("Transmission unreachable; %d torrent(s) will be tidied once it is back.", len(hashes))
            return
        self._reap_failing = False
        done = [h for h in hashes if progress.get(h, 0.0) >= 1.0]
        gone = [h for h in hashes if h not in progress]
        if done and not await remove_torrents(url, user, password, done):
            done = []
        if done:
            _logger.info("Removed %d completed torrent(s) from Transmission; files kept.", len(done))
        if done or gone:
            self.tstate.unwatch(done + gone)
            await self.tstate.save()

    # ── used by the web UI and the CLI ────────────────────────────────────

    def touch(self, entry: MediaEntry, now: float | None = None) -> None:
        """Start an entry's history now, so only episodes airing from here on are tracked."""
        self.tstate.show(entry.title, now)

    def progress(self, entry: MediaEntry) -> dict:
        show = self.tstate.peek(entry.title)
        if show is None:
            return {"searching": [], "review": [], "missed": [], "last": None, "backfill": False, "completed": False}
        grabbed = [(rec.at, number, rec.name) for number, rec in show.episodes.items() if rec.status == GRABBED]
        last = max(grabbed) if grabbed else None
        return {
            "searching": [
                {"episode": number, "since": rec.first_check, "note": rec.note}
                for number, rec in sorted(show.episodes.items())
                if rec.status == PENDING and rec.first_check
            ],
            "review": sorted(n for n, rec in show.episodes.items() if rec.status == REVIEW),
            "missed": sorted(n for n, rec in show.episodes.items() if rec.status == MISSED),
            "last": {"episode": last[1], "name": last[2], "at": last[0]} if last else None,
            "backfill": show.backfill,
            "completed": bool(show.completed_at),
        }

    def reviews(self) -> list[dict]:
        out = []
        for title, episode, rec in self.tstate.reviews():
            entry = self.media_state.get(title)
            if entry is None:
                continue
            out.append({
                "title": title,
                "episode": episode,
                "note": rec.note,
                "since": rec.at,
                "path": resolve_download_dir(self.cfg, entry, self.store.info(entry)),
                "candidates": [{k: v for k, v in c.items() if k != "magnet"} for c in rec.candidates],
            })
        return out

    async def preview(self, entry: MediaEntry, episode: int, query: str | None = None,
                      now: float | None = None) -> MatchResult:
        """What the tracker would make of one episode right now, without adding anything."""
        now = time.time() if now is None else now
        show = self.tstate.peek(entry.title) or ShowState(since = now)
        schedule = dict(self.store.episodes(entry))
        target = self._target(entry, self.store.info(entry), show, episode,
                              schedule.get(episode), schedule.get(episode + 1))
        return await self._search(entry, self.store.info(entry), target, now, query = query, listings = {})

    def _infer_episode(self, entry: MediaEntry, info: AnilistMedia | None, show: ShowState,
                       release: Release, published: int | None, now: float) -> int | None:
        """Which aired episode a hand-picked release is, when that is unambiguous."""
        schedule = self.store.episodes(entry)
        fitting: list[tuple[bool, int]] = []
        for index, (episode, airing_at) in enumerate(schedule):
            if airing_at - EARLY_TOLERANCE > now:
                break
            next_airing = schedule[index + 1][1] if index + 1 < len(schedule) else None
            target = self._target(entry, info, show, episode, airing_at, next_airing)
            if fits(release, target) is None:
                continue
            in_window = (
                published is None
                or (published >= airing_at - EARLY_TOLERANCE and in_episode_window(target, published))
            )
            fitting.append((in_window, episode))
        windowed = [episode for ok, episode in fitting if ok]
        if len(windowed) == 1:
            return windowed[0]
        if len(fitting) == 1:
            return fitting[0][1]
        return None

    def _confirmed_offset(self, entry: MediaEntry, episode: int, release: Release,
                          published: int | None) -> int | None:
        """The numbering offset a person just vouched for, if the upload time backs it up."""
        if release.episode is None or release.batch or published is None:
            return None
        schedule = dict(self.store.episodes(entry))
        airing_at, next_airing = schedule.get(episode), schedule.get(episode + 1)
        if airing_at is None or published < airing_at - EARLY_TOLERANCE:
            return None
        if next_airing is not None and published >= next_airing - EARLY_TOLERANCE:
            return None
        return release.episode - episode

    async def manual_add(self, magnet: str, name: str, entry: MediaEntry | None,
                         episode: int | None = None, published: int | None = None,
                         now: float | None = None) -> dict:
        """Add a torrent picked by hand, filing it under the entry it belongs to."""
        now = time.time() if now is None else now
        release = parse_release(name)
        label = name or "(unnamed torrent)"

        if entry is not None and entry.media_type != "manga":
            info = self.store.info(entry)
            show = self.tstate.show(entry.title, now)
            if episode is None:
                episode = self._infer_episode(entry, info, show, release, published, now)
            if episode is not None:
                rec = self.tstate.episode(entry.title, episode)
                cand = {"magnet": magnet, "name": label, "group": release.group, "reason": "picked by hand"}
                ok, error = await self._grab(
                    entry, info, show, episode, rec, cand, now, kind = "manual",
                    show_offset = self._confirmed_offset(entry, episode, release, published),
                )
                await self.tstate.save()
                directory = resolve_download_dir(self.cfg, entry, info)
                return {"ok": ok, "error": error or None, "dir": directory, "episode": episode}

        if entry is not None:
            download_dir = resolve_download_dir(self.cfg, entry, self.store.info(entry))
            title = entry.title
        else:
            folder = safe_dirname(release.title) if release.title else ""
            download_dir = os.path.join(self.cfg.default_dir, folder) if folder else self.cfg.default_dir
            title = release.title or label
        added, error = await self._send(magnet, download_dir)
        if added is None:
            return {"ok": False, "error": error, "dir": download_dir, "episode": None}
        info_hash = added.hash or magnet_hash(magnet)
        self.tstate.watch(info_hash)
        self.tstate.log("manual", title, name = label, info_hash = info_hash, directory = download_dir,
                        detail = "picked by hand", now = now)
        await self.tstate.save()
        _log(title, f"Added by hand: {label} -> {download_dir}")
        return {"ok": True, "error": None, "dir": download_dir, "episode": None}

    async def approve(self, title: str, episode: int, info_hash: str, now: float | None = None) -> dict:
        """Accept one of the releases held for review."""
        now = time.time() if now is None else now
        entry = self.media_state.get(title)
        show = self.tstate.peek(title)
        rec = show.episodes.get(episode) if show else None
        if entry is None or show is None or rec is None or rec.status != REVIEW:
            return {"ok": False, "error": "nothing is waiting for review there"}
        cand = next((c for c in rec.candidates if c.get("hash") == info_hash), None)
        if cand is None:
            return {"ok": False, "error": "that release is no longer listed; refresh and try again"}
        release = parse_release(cand["name"])
        ok, error = await self._grab(
            entry, self.store.info(entry), show, episode, rec, {**cand, "reason": "approved after review"}, now,
            kind = "approved",
            show_offset = self._confirmed_offset(entry, episode, release, cand.get("published")),
        )
        await self.tstate.save()
        return {"ok": ok, "error": error or None}

    async def skip(self, title: str, episode: int, now: float | None = None) -> dict:
        show = self.tstate.peek(title)
        rec = show.episodes.get(episode) if show else None
        if rec is None or rec.status in (GRABBED, SKIPPED):
            return {"ok": False, "error": "nothing to skip there"}
        rec.status = SKIPPED
        rec.at = time.time() if now is None else now
        rec.candidates = []
        rec.note = ""
        self.tstate.log("skipped", title, episode, detail = "skipped by hand", now = now)
        await self.tstate.save()
        _log(title, f"Ep {_pad(episode)}: skipped.")
        return {"ok": True, "error": None}

    async def retry(self, title: str, episode: int) -> dict:
        show = self.tstate.peek(title)
        if show is None or episode not in show.episodes:
            return {"ok": False, "error": "no record of that episode"}
        show.episodes[episode] = EpisodeRecord()
        await self.tstate.save()
        _log(title, f"Ep {_pad(episode)}: queued again.")
        return {"ok": True, "error": None}

    async def set_backfill(self, title: str, now: float | None = None) -> dict:
        """Also fetch the episodes that aired before the entry was added."""
        entry = self.media_state.get(title)
        if entry is None or entry.media_type == "manga":
            return {"ok": False, "error": "not a tracked anime"}
        show = self.tstate.show(title, now)
        show.backfill = True
        show.completed_at = 0.0
        error = None
        try:
            await self.store.fetch_full_schedule(entry)
        except AnilistError as exc:
            error = f"AniList: {exc}"
        await self.tstate.save()
        _log(title, "Earlier episodes queued.")
        return {"ok": True, "error": error}
