from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import pytest

from anilist.types import AnilistMedia, MediaStatus, MediaType
from lydarr.config import AppConfig
from lydarr.file_manager import MediaEntry, MediaState
from lydarr.media_store import MediaStore
from lydarr.state import GRABBED, MISSED, PENDING, REVIEW, SKIPPED, TrackerState
from lydarr.tests.fake_jellyfin import KEY, URL, FakeJellyfin
from lydarr.torrent_client import AddedTorrent, TorrentStatus
from lydarr.tracker import (
    ADD_RETRY, DEFAULT_CHECK_DELAY, GIVE_UP_AFTER, MANGA_POLL, MANGA_RETRY, MAX_CHECKS_PER_TICK, SCAN_DELAY,
    Tracker, _log, _pad, retry_interval,
)
from nyaa.types import HttpError, Torrent, TorrentType

HOUR = 3600
DAY = 86400
WEEK = 7 * DAY
NOW = 1_800_000_000
SHOW_ID = 101

_counter = 0


def _torrent(name: str, published: int, seeders: int = 100) -> Torrent:
    global _counter
    _counter += 1
    return Torrent(
        id = str(_counter), category = "", url = "", name = name, download_url = "",
        magnet = f"magnet:?xt=urn:btih:{_counter:040x}",
        size = "1.4 GiB",
        date = format_datetime(datetime.fromtimestamp(published, tz = timezone.utc)),
        seeders = seeders, leechers = 0, completed = None, type = TorrentType.TRUSTED,
    )


def _media(status: MediaStatus = MediaStatus.RELEASING, episodes: int | None = 12, **extra) -> AnilistMedia:
    return AnilistMedia(
        id = SHOW_ID, title_english = "The Show Season 2", title_romaji = "Shou 2nd Season",
        media_type = MediaType.ANIME, status = status, episodes = episodes, chapters = None,
        next_airing_at = None, next_airing_episode = None, **extra,
    )


def _entry(**overrides) -> MediaEntry:
    base = dict(title = "The Show Season 2", anilist_id = SHOW_ID, search_name = "Shou")
    base.update(overrides)
    return MediaEntry(**base)


class Rig:
    def __init__(self, tmp_path, entries, schedule, media = None, prequels = None, since = NOW - 30 * DAY):
        self.cfg = AppConfig(
            anime_file = str(tmp_path / "anime.toml"),
            transmission_url = "http://transmission.invalid/rpc",
            transmission_user = None, transmission_pass = None,
            default_dir = str(tmp_path / "library"), default_dir_set = True,
            lydarr_user = None, lydarr_pass = None,
        )
        self.state = MediaState(entries)
        self.store = MediaStore(self.cfg.cache_file, self.state, self.cfg.anime_file)
        for m in (media if media is not None else [_media()]):
            self.store._media[m.id] = m
        self.store._schedule = {SHOW_ID: dict(schedule)}
        self.store._prequels = {SHOW_ID: [12] if prequels is None else prequels}
        self.store.refresh_soon = lambda force = False: None
        self.tstate = TrackerState(self.cfg.state_file)
        self.tracker = Tracker(self.cfg, self.state, self.store, self.tstate)
        if since is not None:
            for e in entries:
                self.tstate.show(e.title, since)

    def record(self, episode: int, title: str = "The Show Season 2"):
        show = self.tstate.peek(title)
        return show.episodes.get(episode) if show else None


@contextmanager
def _world(torrents = None, added = "auto"):
    """Stand-ins for Nyaa and Transmission."""
    async def fake_add(url, user, password, magnet, download_dir = None):
        return AddedTorrent(id = 1, hash = magnet.rsplit(":", 1)[-1], name = "x")

    add = AsyncMock(side_effect = fake_add) if added == "auto" else AsyncMock(return_value = added)
    with patch("lydarr.tracker.find_candidates", new_callable = AsyncMock, return_value = torrents or []) as find, \
         patch("lydarr.tracker.add_magnet", add), \
         patch("lydarr.tracker._pause", new_callable = AsyncMock):
        yield find, add


EP4_AIRED = NOW - 3 * HOUR
HEVC_EP4 = "[SubsPlease] Shou S2 - 04 (1080p HEVC) [AAAAAAAA].mkv"


# ── what counts as due ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_due_episode_is_grabbed_into_the_entrys_own_path(tmp_path):
    target_dir = str(tmp_path / "Jellyfin" / "The Show" / "Season 2")
    rig = Rig(tmp_path, [_entry(download_dir = target_dir)], {4: EP4_AIRED})

    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (find, add):
        await rig.tracker.tick(NOW)

    assert find.await_count == 1
    assert add.await_args.args[4] == target_dir
    rec = rig.record(4)
    assert rec.status == GRABBED
    assert rec.name == HEVC_EP4
    assert rig.tstate.reap_hashes() == [rec.hash]
    event = rig.tstate.activity()[0]
    assert (event["kind"], event["episode"], event["dir"], event["name"]) == ("grabbed", 4, target_dir, HEVC_EP4)


@pytest.mark.asyncio
async def test_default_folder_comes_from_the_anilist_title(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (_, add):
        await rig.tracker.tick(NOW)
    assert add.await_args.args[4] == str(tmp_path / "library" / "The Show Season 2")


@pytest.mark.asyncio
async def test_nothing_is_searched_before_the_check_delay(tmp_path):
    rig = Rig(tmp_path, [_entry(check_delay = 240)], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        assert find.await_count == 0
        await rig.tracker.tick(NOW + 2 * HOUR)
        assert find.await_count == 1


def test_default_check_delay_is_one_hour():
    assert DEFAULT_CHECK_DELAY == 60


@pytest.mark.asyncio
async def test_episodes_from_before_tracking_started_are_left_alone(tmp_path):
    rig = Rig(tmp_path, [_entry()], {3: NOW - WEEK, 4: EP4_AIRED}, since = NOW - DAY)
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert find.await_count == 1
    assert rig.record(3) is None
    assert rig.record(4).status == PENDING


@pytest.mark.asyncio
async def test_a_new_entry_starts_tracking_from_now(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED}, since = None)
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert find.await_count == 0
    assert rig.tstate.peek("The Show Season 2").since == NOW


@pytest.mark.asyncio
async def test_archived_manga_and_unresolved_entries_are_skipped(tmp_path):
    entries = [
        _entry(title = "Archived", deprecated = True),
        _entry(title = "Unresolved", anilist_id = 0),
    ]
    rig = Rig(tmp_path, entries, {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert find.await_count == 0


# ── live settings, catch-up, retries ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_path_edits_apply_to_the_very_next_check(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    edited = str(tmp_path / "moved")
    with _world() as (_, add):
        await rig.tracker.tick(NOW)
        await rig.state.update_entry(rig.cfg.anime_file, "The Show Season 2", [], "Shou", download_dir = edited)
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (_, add):
        await rig.tracker.tick(NOW + HOUR)
    assert add.await_args.args[4] == edited


@pytest.mark.asyncio
async def test_removing_an_entry_stops_it_immediately(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    await rig.state.remove(rig.cfg.anime_file, "The Show Season 2")
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (find, add):
        await rig.tracker.tick(NOW)
    assert find.await_count == 0
    assert add.await_count == 0


@pytest.mark.asyncio
async def test_a_missing_release_does_not_block_later_episodes(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: NOW - WEEK - 3 * HOUR, 5: EP4_AIRED})
    ep5 = _torrent("[SubsPlease] Shou S2 - 05 (1080p HEVC) [BBBBBBBB].mkv", EP4_AIRED + 1800)

    with _world([ep5]) as (_, add):
        await rig.tracker.tick(NOW)

    assert rig.record(4).status == PENDING
    assert rig.record(5).status == GRABBED
    assert add.await_count == 1


@pytest.mark.asyncio
async def test_episodes_aired_while_stopped_are_caught_up(tmp_path):
    schedule = {4: NOW - 2 * WEEK, 5: NOW - WEEK, 6: EP4_AIRED}
    rig = Rig(tmp_path, [_entry()], schedule)
    torrents = [
        _torrent(f"[SubsPlease] Shou S2 - {n:02d} (1080p HEVC) [CCCCCCCC].mkv", schedule[n] + 1800)
        for n in (4, 5, 6)
    ]
    with _world(torrents) as (_, add):
        await rig.tracker.tick(NOW)
    assert [rig.record(n).status for n in (4, 5, 6)] == [GRABBED, GRABBED, GRABBED]
    assert add.await_count == 3


@pytest.mark.asyncio
async def test_checks_are_spaced_by_the_retry_timer(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        await rig.tracker.tick(NOW + 5 * 60)
        assert find.await_count == 1
        await rig.tracker.tick(NOW + 16 * 60)
        assert find.await_count == 2


def test_retry_interval_backs_off():
    assert retry_interval(0) == 15 * 60
    assert retry_interval(3 * HOUR) == 30 * 60
    assert retry_interval(DAY) == HOUR
    assert retry_interval(5 * DAY) == 6 * HOUR


@pytest.mark.asyncio
async def test_gives_up_after_two_weeks_and_says_so(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        await rig.tracker.tick(NOW + GIVE_UP_AFTER + 60)
        assert find.await_count == 1
    assert rig.record(4).status == MISSED
    assert rig.tstate.activity()[0]["kind"] == "missed"


@pytest.mark.asyncio
async def test_one_pass_checks_a_bounded_number_of_episodes(tmp_path):
    schedule = {n: NOW - (20 - n) * DAY for n in range(1, 13)}
    rig = Rig(tmp_path, [_entry()], schedule)
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        assert find.await_count == MAX_CHECKS_PER_TICK
        await rig.tracker.tick(NOW + 60)
        assert find.await_count == 12


@pytest.mark.asyncio
async def test_nyaa_failure_is_noted_and_retried(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world() as (find, add):
        find.side_effect = HttpError("timed out")
        await rig.tracker.tick(NOW)
    rec = rig.record(4)
    assert rec.status == PENDING
    assert "Nyaa search failed" in rec.note
    assert rec.next_check == NOW + 15 * 60


@pytest.mark.asyncio
async def test_rescheduled_episode_starts_over_when_it_really_airs(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        assert rig.record(4).checks == 1
        rig.store._schedule[SHOW_ID][4] = NOW + WEEK
        await rig.tracker.tick(NOW + HOUR)
    assert rig.record(4) is None


# ── Transmission problems ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transmission_refusal_keeps_the_episode_pending(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)], added = None):
        await rig.tracker.tick(NOW)
    rec = rig.record(4)
    assert rec.status == PENDING
    assert rec.next_check == NOW + ADD_RETRY
    assert "Transmission did not accept" in rec.note
    assert rig.tstate.activity() == []


@pytest.mark.asyncio
async def test_unwritable_path_is_reported_not_swapped_for_the_default(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rig = Rig(tmp_path, [_entry(download_dir = str(blocker / "Season 2"))], {4: EP4_AIRED})
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (_, add):
        await rig.tracker.tick(NOW)
    assert add.await_count == 0
    assert rig.record(4).status == PENDING
    assert "cannot create" in rig.record(4).note


# ── review ───────────────────────────────────────────────────────────────────

ABSOLUTE_EP4 = "[SubsPlease] Shou - 16 (1080p HEVC) [DDDDDDDD].mkv"


@pytest.mark.asyncio
async def test_unexpected_numbering_is_held_then_learned_on_approval(tmp_path):
    rig = Rig(tmp_path, [_entry(submitters = ["SubsPlease"])], {4: EP4_AIRED, 5: EP4_AIRED + WEEK}, prequels = [])
    held = _torrent(ABSOLUTE_EP4, EP4_AIRED + 1800)

    with _world([held]) as (_, add):
        await rig.tracker.tick(EP4_AIRED + 90 * 60)
        assert rig.record(4).status == PENDING
        await rig.tracker.tick(NOW + 3 * HOUR)
        assert add.await_count == 0

    rec = rig.record(4)
    assert rec.status == REVIEW
    [review] = rig.tracker.reviews()
    assert (review["title"], review["episode"]) == ("The Show Season 2", 4)
    assert review["candidates"][0]["name"] == ABSOLUTE_EP4
    assert "magnet" not in review["candidates"][0]
    assert [e["kind"] for e in rig.tstate.activity()] == ["review"]

    with _world() as (_, add):
        result = await rig.tracker.approve("The Show Season 2", 4, held.info_hash, now = NOW + 4 * HOUR)
    assert result == {"ok": True, "error": None}
    assert add.await_args.args[3] == held.magnet
    show = rig.tstate.peek("The Show Season 2")
    assert rig.record(4).status == GRABBED
    assert show.extra_offsets == [12]
    assert show.offsets == {"subsplease": 12}
    assert rig.tstate.activity()[0]["kind"] == "approved"

    next_week = _torrent("[SubsPlease] Shou - 17 (1080p HEVC) [EEEEEEEE].mkv", EP4_AIRED + WEEK + 1800)
    with _world([next_week]) as (_, add):
        await rig.tracker.tick(NOW + WEEK)
    assert rig.record(5).status == GRABBED


@pytest.mark.asyncio
async def test_review_clears_itself_when_a_proper_release_appears(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED}, prequels = [])
    with _world([_torrent(ABSOLUTE_EP4, EP4_AIRED + 1800)]):
        await rig.tracker.tick(NOW + 3 * HOUR)
    assert rig.record(4).status == REVIEW
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 4 * HOUR)]):
        await rig.tracker.tick(NOW + 4 * HOUR)
    assert rig.record(4).status == GRABBED
    assert rig.tracker.reviews() == []


@pytest.mark.asyncio
async def test_approve_rejects_unknown_items(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    assert (await rig.tracker.approve("The Show Season 2", 4, "abc"))["ok"] is False
    assert (await rig.tracker.approve("Nope", 1, "abc"))["ok"] is False


@pytest.mark.asyncio
async def test_skip_settles_an_episode_and_retry_reopens_it(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
        assert (await rig.tracker.skip("The Show Season 2", 4))["ok"] is True
        assert rig.record(4).status == SKIPPED
        await rig.tracker.tick(NOW + DAY)
        assert find.await_count == 1

        assert (await rig.tracker.retry("The Show Season 2", 4))["ok"] is True
        await rig.tracker.tick(NOW + DAY + 60)
        assert find.await_count == 2
    assert (await rig.tracker.skip("The Show Season 2", 99))["ok"] is False


# ── finales and backfill ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finale_archives_the_entry_without_downloading_again(tmp_path):
    rig = Rig(tmp_path, [_entry()], {12: EP4_AIRED}, media = [_media(MediaStatus.FINISHED)])
    final = _torrent("[SubsPlease] Shou S2 - 12 (1080p HEVC) [FFFFFFFF].mkv", EP4_AIRED + 1800)

    with _world([final]) as (find, add):
        await rig.tracker.tick(NOW)
        await rig.tracker.tick(NOW + 60)

    assert add.await_count == 1
    assert find.await_count == 1
    assert rig.state.get("The Show Season 2").deprecated is True
    assert rig.tracker.progress(rig.state.get("The Show Season 2"))["completed"] is True
    assert rig.tstate.activity()[0]["kind"] == "completed"


@pytest.mark.asyncio
async def test_finished_show_with_nothing_tracked_is_left_in_place(tmp_path):
    rig = Rig(tmp_path, [_entry()], {12: NOW - 60 * DAY}, media = [_media(MediaStatus.FINISHED)])
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert find.await_count == 0
    assert rig.state.get("The Show Season 2").deprecated is False


@pytest.mark.asyncio
async def test_finale_waits_for_unsettled_episodes(tmp_path):
    rig = Rig(tmp_path, [_entry()], {11: NOW - WEEK, 12: EP4_AIRED}, media = [_media(MediaStatus.FINISHED)])
    final = _torrent("[SubsPlease] Shou S2 - 12 (1080p HEVC) [FFFFFFFF].mkv", EP4_AIRED + 1800)
    with _world([final]):
        await rig.tracker.tick(NOW)
    assert rig.record(12).status == GRABBED
    assert rig.record(11).status == PENDING
    assert rig.state.get("The Show Season 2").deprecated is False


@pytest.mark.asyncio
async def test_backfill_queues_episodes_from_before_the_entry_was_added(tmp_path):
    rig = Rig(tmp_path, [_entry()], {1: NOW - 3 * WEEK, 2: NOW - 2 * WEEK}, since = NOW)
    with patch.object(rig.store, "fetch_full_schedule", new_callable = AsyncMock) as fetch:
        result = await rig.tracker.set_backfill("The Show Season 2", now = NOW)
    assert result == {"ok": True, "error": None}
    fetch.assert_awaited_once()

    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert find.await_count == 2
    assert rig.tracker.progress(rig.state.get("The Show Season 2"))["backfill"] is True


@pytest.mark.asyncio
async def test_backfill_refuses_manga_and_unknown_titles(tmp_path):
    rig = Rig(tmp_path, [_entry(title = "Book", media_type = "manga")], {})
    assert (await rig.tracker.set_backfill("Book"))["ok"] is False
    assert (await rig.tracker.set_backfill("Nope"))["ok"] is False


# ── picking by hand ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hand_picked_torrent_goes_to_the_entry_path_and_settles_the_episode(tmp_path):
    target_dir = str(tmp_path / "Jellyfin" / "Season 2")
    rig = Rig(tmp_path, [_entry(download_dir = target_dir)], {4: EP4_AIRED})
    picked = _torrent(HEVC_EP4, EP4_AIRED + 1800)

    with _world() as (find, add):
        result = await rig.tracker.manual_add(picked.magnet, picked.name, rig.state.get("The Show Season 2"),
                                              published = picked.published, now = NOW)
        await rig.tracker.tick(NOW + 60)

    assert result == {"ok": True, "error": None, "dir": target_dir, "episode": 4}
    assert add.await_args.args[4] == target_dir
    assert find.await_count == 0
    assert rig.tstate.activity()[0]["kind"] == "manual"


@pytest.mark.asyncio
async def test_hand_picked_torrent_without_an_entry_gets_a_folder_from_its_own_title(tmp_path):
    rig = Rig(tmp_path, [], {})
    with _world() as (_, add):
        result = await rig.tracker.manual_add(
            "magnet:?xt=urn:btih:" + "b" * 40, "[Group] Some Other Show - 03 [1080p]", None, now = NOW,
        )
    assert result["dir"] == str(tmp_path / "library" / "Some Other Show")
    assert add.await_args.args[4] == result["dir"]
    assert rig.tstate.reap_hashes() == ["b" * 40]


@pytest.mark.asyncio
async def test_hand_picked_batch_is_filed_under_the_entry_without_touching_episodes(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world():
        result = await rig.tracker.manual_add(
            "magnet:?xt=urn:btih:" + "c" * 40, "[Group] Shou S2 (01-12) [1080p][Batch]",
            rig.state.get("The Show Season 2"), now = NOW,
        )
    assert result["dir"] == str(tmp_path / "library" / "The Show Season 2")
    assert result["episode"] is None
    assert rig.record(4) is None


@pytest.mark.asyncio
async def test_explicit_episode_teaches_numbering_only_when_upload_time_agrees(tmp_path):
    entry = _entry(submitters = ["SubsPlease"])
    schedule = {4: EP4_AIRED - WEEK, 5: EP4_AIRED}

    rig = Rig(tmp_path, [entry], schedule, prequels = [])
    this_week = _torrent("[SubsPlease] Shou - 17 (1080p) [AAAAAAAA].mkv", EP4_AIRED + 1800)
    with _world():
        await rig.tracker.manual_add(this_week.magnet, this_week.name, entry, episode = 5,
                                     published = this_week.published, now = NOW)
    assert rig.tstate.peek(entry.title).extra_offsets == [12]

    other = Rig(tmp_path / "other", [entry], schedule, prequels = [])
    last_week = _torrent("[SubsPlease] Shou - 16 (1080p) [BBBBBBBB].mkv", EP4_AIRED - WEEK + 1800)
    with _world():
        await other.tracker.manual_add(last_week.magnet, last_week.name, entry, episode = 5,
                                       published = last_week.published, now = NOW)
    assert other.record(5).status == GRABBED
    assert other.tstate.peek(entry.title).extra_offsets == []
    assert other.tstate.peek(entry.title).offsets == {}


@pytest.mark.asyncio
async def test_hand_pick_reports_transmission_failure(tmp_path):
    rig = Rig(tmp_path, [], {})
    with _world(added = None):
        result = await rig.tracker.manual_add("magnet:?xt=urn:btih:" + "d" * 40, "[G] X - 01", None)
    assert result["ok"] is False
    assert "Transmission" in result["error"]


# ── finished torrents ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reap_removes_completed_and_forgets_vanished_torrents(tmp_path):
    rig = Rig(tmp_path, [], {})
    for h in ("done", "half", "gone"):
        rig.tstate.watch(h)

    with patch("lydarr.tracker.torrent_progress", new_callable = AsyncMock,
               return_value = {"done": TorrentStatus(1.0), "half": TorrentStatus(0.5)}), \
         patch("lydarr.tracker.remove_torrents", new_callable = AsyncMock, return_value = True) as remove:
        await rig.tracker.reap()

    assert remove.await_args.args[3] == ["done"]
    assert rig.tstate.reap_hashes() == ["half"]


@pytest.mark.asyncio
async def test_reap_waits_quietly_while_transmission_is_down(tmp_path, caplog):
    rig = Rig(tmp_path, [], {})
    rig.tstate.watch("abc")
    with patch("lydarr.tracker.torrent_progress", new_callable = AsyncMock, return_value = None), \
         patch("lydarr.tracker.remove_torrents", new_callable = AsyncMock) as remove, \
         caplog.at_level("INFO", logger = "lydarr.tracker"):
        for _ in range(5):
            await rig.tracker.reap()

    assert remove.await_count == 0
    assert rig.tstate.reap_hashes() == ["abc"]
    assert caplog.text.count("Transmission unreachable") == 1


@pytest.mark.asyncio
async def test_reap_keeps_watching_when_removal_fails(tmp_path):
    rig = Rig(tmp_path, [], {})
    rig.tstate.watch("done")
    with patch("lydarr.tracker.torrent_progress", new_callable = AsyncMock,
               return_value = {"done": TorrentStatus(1.0)}), \
         patch("lydarr.tracker.remove_torrents", new_callable = AsyncMock, return_value = False):
        await rig.tracker.reap()
    assert rig.tstate.reap_hashes() == ["done"]


# ── Jellyfin ─────────────────────────────────────────────────────────────────

EP4_FILE = "[SubsPlease] Shou S2 - 04 (1080p HEVC) [AAAAAAAA].mkv"
EP5_FILE = "[SubsPlease] Shou S2 - 05 (1080p HEVC) [BBBBBBBB].mkv"


def _jellyfin(rig: Rig, tmp_path, *, libraries: dict | None = None, path_map: list | None = None) -> FakeJellyfin:
    rig.cfg.jellyfin_url, rig.cfg.jellyfin_api_key = URL, KEY
    rig.cfg.jellyfin_path_map = list(path_map or [])
    return FakeJellyfin({"Anime": [str(tmp_path / "library")]} if libraries is None else libraries)


async def _finish(rig: Rig, info_hash: str, folder, name: str) -> None:
    """Transmission finishes a torrent lydarr added."""
    rig.tstate.watch(info_hash)
    status = TorrentStatus(done = 1.0, name = name, download_dir = str(folder))
    with patch("lydarr.tracker.torrent_progress", new_callable = AsyncMock, return_value = {info_hash: status}), \
         patch("lydarr.tracker.remove_torrents", new_callable = AsyncMock, return_value = True):
        await rig.tracker.reap(now = NOW)


async def _scan(rig: Rig, jf: FakeJellyfin, at: float = NOW + SCAN_DELAY) -> None:
    with patch("lydarr.jellyfin._request", jf):
        await rig.tracker.scan_jellyfin(at)


def _show_rig(tmp_path, **jellyfin) -> tuple[Rig, FakeJellyfin, str, object]:
    """An entry saving into a season folder of a show Jellyfin already lists."""
    season = tmp_path / "library" / "The Show" / "Season 2"
    rig = Rig(tmp_path, [_entry(download_dir = str(season))], {4: EP4_AIRED})
    jf = _jellyfin(rig, tmp_path, **jellyfin)
    show_root = jellyfin.get("libraries", {}).get("Anime", [str(tmp_path / "library")])[0]
    show = jf.add_show("Anime", "The Show", f"{show_root}/The Show")
    return rig, jf, show, season


@pytest.mark.asyncio
async def test_a_finished_download_has_its_show_scanned_once(tmp_path, caplog):
    rig, jf, show, season = _show_rig(tmp_path)
    await _finish(rig, "h4", season, EP4_FILE)

    await _scan(rig, jf, NOW + SCAN_DELAY - 1)
    assert jf.calls == []
    with caplog.at_level("INFO", logger = "lydarr.tracker"):
        await _scan(rig, jf)
        await _scan(rig, jf, NOW + 10 * SCAN_DELAY)

    assert jf.scans == [show]
    assert "Asked Jellyfin to scan The Show." in caplog.text
    assert rig.tstate.activity() == []


@pytest.mark.asyncio
async def test_a_show_new_to_jellyfin_gets_its_library_scanned(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    jf = _jellyfin(rig, tmp_path)
    await _finish(rig, "h4", tmp_path / "library" / "The Show Season 2", EP4_FILE)
    await _scan(rig, jf)
    assert jf.scans == ["lib-Anime"]


@pytest.mark.asyncio
async def test_downloads_of_one_show_finishing_together_share_a_scan(tmp_path):
    rig, jf, show, season = _show_rig(tmp_path)
    await _finish(rig, "h4", season, EP4_FILE)
    await _finish(rig, "h5", season, EP5_FILE)
    await _scan(rig, jf)
    assert jf.scans == [show]


@pytest.mark.asyncio
@pytest.mark.parametrize("location, path_map", [
    ("/media/Anime", "explicit"),
    ("/media/library", None),
])
async def test_jellyfin_may_see_the_files_under_another_root(tmp_path, location, path_map):
    pairs = [(str(tmp_path / "library"), location)] if path_map else []
    rig, jf, show, season = _show_rig(tmp_path, libraries = {"Anime": [location]}, path_map = pairs)
    await _finish(rig, "h4", season, EP4_FILE)
    await _scan(rig, jf)
    assert jf.scans == [show]


@pytest.mark.asyncio
async def test_a_download_outside_every_library_is_only_logged(tmp_path, caplog):
    folder = tmp_path / "library" / "The Show Season 2"
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    jf = _jellyfin(rig, tmp_path, libraries = {"Anime": ["/srv/anime"]})
    await _finish(rig, "h4", folder, EP4_FILE)
    with caplog.at_level("WARNING", logger = "lydarr.tracker"):
        await _scan(rig, jf)
    assert jf.scans == []
    assert f"no library holds {folder / EP4_FILE}; see JELLYFIN_PATH_MAP" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("trouble", ["down", "wrong key"])
async def test_jellyfin_trouble_is_logged_and_the_scan_dropped(tmp_path, caplog, trouble):
    rig, jf, show, season = _show_rig(tmp_path)
    jf.down = trouble == "down"
    if trouble == "wrong key":
        rig.cfg.jellyfin_api_key = "wrong"
    await _finish(rig, "h4", season, EP4_FILE)
    with caplog.at_level("WARNING", logger = "lydarr.tracker"):
        await _scan(rig, jf)
        calls = len(jf.calls)
        await _scan(rig, jf, NOW + 10 * SCAN_DELAY)
    assert jf.scans == []
    assert len(jf.calls) == calls == 1
    assert ("cannot reach" if trouble == "down" else "JELLYFIN_API_KEY") in caplog.text


@pytest.mark.asyncio
async def test_nothing_is_lined_up_for_jellyfin_unless_it_is_set_up(tmp_path):
    rig, jf, show, season = _show_rig(tmp_path)
    rig.cfg.jellyfin_url = ""
    await _finish(rig, "h4", season, EP4_FILE)
    rig.cfg.jellyfin_url = URL
    await _scan(rig, jf)
    assert jf.calls == []


# ── queries and summaries ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_name_is_the_only_query_when_set(tmp_path):
    rig = Rig(tmp_path, [_entry(search_name = "Shou S2")], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert [c.args[0] for c in find.await_args_list] == ["Shou S2"]


@pytest.mark.asyncio
async def test_without_a_search_name_romaji_and_title_are_tried_without_season_words(tmp_path):
    rig = Rig(tmp_path, [_entry(search_name = "")], {4: EP4_AIRED})
    with _world() as (find, _):
        await rig.tracker.tick(NOW)
    assert [c.args[0] for c in find.await_args_list] == ["Shou", "The Show"]
    assert find.await_args.args[1] == [4, 16]


@pytest.mark.asyncio
async def test_progress_reports_searching_and_last_grab(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: NOW - WEEK - 3 * HOUR, 5: EP4_AIRED})
    ep5 = _torrent("[SubsPlease] Shou S2 - 05 (1080p HEVC) [BBBBBBBB].mkv", EP4_AIRED + 1800)
    with _world([ep5]):
        await rig.tracker.tick(NOW)

    progress = rig.tracker.progress(rig.state.get("The Show Season 2"))
    assert [s["episode"] for s in progress["searching"]] == [4]
    assert progress["last"]["episode"] == 5
    assert progress["review"] == [] and progress["missed"] == []
    assert rig.tracker.progress(_entry(title = "Unknown")) == {
        "searching": [], "review": [], "missed": [], "last": None, "backfill": False, "completed": False,
    }


@pytest.mark.asyncio
async def test_state_survives_a_restart(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]):
        await rig.tracker.tick(NOW)

    reopened = TrackerState(rig.cfg.state_file)
    reopened.load()
    assert reopened.peek("The Show Season 2").episodes[4].status == GRABBED
    assert reopened.reap_hashes() == rig.tstate.reap_hashes()
    assert reopened.activity()[0]["kind"] == "grabbed"


@pytest.mark.asyncio
async def test_preview_judges_without_adding(tmp_path):
    rig = Rig(tmp_path, [_entry()], {4: EP4_AIRED})
    with _world([_torrent(HEVC_EP4, EP4_AIRED + 1800)]) as (find, add):
        result = await rig.tracker.preview(rig.state.get("The Show Season 2"), 4, query = "typed query", now = NOW)
    assert result.best.torrent.name == HEVC_EP4
    assert find.await_args.args[0] == "typed query"
    assert add.await_count == 0
    assert rig.record(4) is None


# ── manga (experimental) ─────────────────────────────────────────────────────

def _manga_rig(tmp_path, status: MediaStatus, chapters: int | None, last_chapter: int = 0) -> Rig:
    book = AnilistMedia(
        id = SHOW_ID, title_english = "Book", title_romaji = "Book", media_type = MediaType.MANGA,
        status = status, episodes = None, chapters = chapters, next_airing_at = None, next_airing_episode = None,
    )
    entry = MediaEntry(title = "Book", media_type = "manga", anilist_id = SHOW_ID, last_chapter = last_chapter)
    return Rig(tmp_path, [entry], {}, media = [book])


@pytest.mark.asyncio
async def test_manga_downloads_new_chapters_in_order_then_archives_when_finished(tmp_path):
    rig = _manga_rig(tmp_path, MediaStatus.FINISHED, chapters = 3, last_chapter = 1)
    chapters = [_torrent("[A] Book 002", NOW), _torrent("[A] Book 003", NOW)]

    with _world() as (_, add), \
         patch("lydarr.tracker.search_chapter", new_callable = AsyncMock, side_effect = chapters):
        pause = await rig.tracker._step_manga(rig.state.get("Book"))

    assert pause is None
    assert add.await_count == 2
    assert rig.state.get("Book").last_chapter == 3
    assert rig.state.get("Book").deprecated is True


@pytest.mark.asyncio
async def test_manga_waits_when_a_chapter_is_not_out(tmp_path):
    rig = _manga_rig(tmp_path, MediaStatus.FINISHED, chapters = 2)
    with _world(), patch("lydarr.tracker.search_chapter", new_callable = AsyncMock, return_value = None):
        assert await rig.tracker._step_manga(rig.state.get("Book")) == MANGA_RETRY
    assert rig.state.get("Book").last_chapter == 0


@pytest.mark.asyncio
async def test_ongoing_manga_without_a_chapter_count_just_polls(tmp_path):
    rig = _manga_rig(tmp_path, MediaStatus.RELEASING, chapters = None)
    with _world() as (_, add), patch("lydarr.tracker.search_chapter", new_callable = AsyncMock) as search:
        assert await rig.tracker._step_manga(rig.state.get("Book")) == MANGA_POLL
    assert search.await_count == 0 and add.await_count == 0


# ── small helpers ────────────────────────────────────────────────────────────

def test_log_emits_tagged_message(caplog):
    with caplog.at_level("INFO", logger = "lydarr.tracker"):
        _log("Solo Leveling", "Test message")
    assert "[Solo Leveling] Test message" in caplog.text


def test_pad():
    assert _pad(1) == "01"
    assert _pad(25) == "25"
