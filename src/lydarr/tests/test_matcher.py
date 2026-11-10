from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from lydarr.matcher import (
    MATCH, NEAR, REJECTED, WAITING, Policy, Target,
    accepted_numbers, cumulative_offsets, evaluate, expected_seasons, group_key,
)
from nyaa.types import Torrent, TorrentType

HOUR = 3600


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo = timezone.utc).timestamp())


AIRED = _ts("2026-10-23 14:30")
DUE = AIRED + 145 * 60

_counter = 0


def _torrent(name: str, when: str | None, seeders: int = 100, trusted: bool = False) -> Torrent:
    global _counter
    _counter += 1
    date = format_datetime(datetime.fromtimestamp(_ts(when), tz = timezone.utc)) if when else ""
    return Torrent(
        id = str(_counter),
        category = "Anime - English-translated",
        url = f"https://nyaa.si/view/{_counter}",
        name = name,
        download_url = "",
        magnet = f"magnet:?xt=urn:btih:{_counter:040x}",
        size = "1.4 GiB",
        date = date,
        seeders = seeders,
        leechers = 0,
        completed = None,
        type = TorrentType.TRUSTED if trusted else TorrentType.NORMAL,
    )


ERAI_AVC_1080 = "[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04 [1080p CR WEB-DL AVC AAC][MultiSub][6607CA8D]"
ERAI_AVC_720 = "[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04 [720p CR WEB-DL AVC AAC][MultiSub][4C185C89]"
ERAI_AVC_480 = "[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04 [480p CR WEB-DL AVC AAC][MultiSub][F4B62AC5]"
ERAI_HEVC = "[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04 [1080p CR WEBRip HEVC AAC][MultiSub][74CA08B6]"
ERAI_S2_HEVC = "[Erai-raws] Kusuriya no Hitorigoto 2nd Season - 04 [1080p CR WEBRip HEVC EAC3][MultiSub][61D3542F]"
SUBSPLEASE_ABS = "[SubsPlease] Kusuriya no Hitorigoto - 52 (1080p) [0A0A0A0A].mkv"
ASW_ABS = "[ASW] Kusuriya no Hitorigoto - 52 [1080p HEVC x265 10Bit][AAC]"
HALF_BAKED = "[Half-Baked] The Apothecary Diaries - S03E04 [WEB 1080p HEVC AAC] | Kusuriya no Hitorigoto - 52"

NAMES = ("Kusuriya no Hitorigoto 3rd Season", "The Apothecary Diaries Season 3")


def _target(**overrides) -> Target:
    base = dict(
        episode = 4,
        airing_at = AIRED,
        due_at = DUE,
        names = NAMES,
        search_name = "Kusuriya no Hitorigoto",
        seasons = frozenset({3}),
        prequel_counts = (24, 24),
        submitters = ("SubsPlease", "Erai-raws"),
    )
    base.update(overrides)
    return Target(**base)


def _week_one() -> list[Torrent]:
    return [
        _torrent(ERAI_AVC_1080, "2026-10-23 15:00", 882, trusted = True),
        _torrent(ERAI_AVC_720, "2026-10-23 15:01", 98, trusted = True),
        _torrent(ERAI_AVC_480, "2026-10-23 15:01", 27, trusted = True),
        _torrent(ERAI_S2_HEVC, "2025-01-31 23:22", 53, trusted = True),
        _torrent(SUBSPLEASE_ABS, "2026-10-23 15:05", 2400, trusted = True),
        _torrent(ASW_ABS, "2026-10-23 16:27", 327),
    ]


def _verdicts(result) -> dict[str, str]:
    return {c.torrent.name: c.verdict for c in result.ordered()}


def test_last_seasons_same_numbered_hevc_is_never_picked():
    """The bug this module exists for: at the first check only the old season has an HEVC '- 04'."""
    result = evaluate(_target(submitters = ("Erai-raws",)), _week_one(), DUE, Policy())

    old = next(c for c in result.rejected if c.torrent.name == ERAI_S2_HEVC)
    assert old.reason == "uploaded before this episode aired"
    assert result.best is None
    assert result.waiting[0].reason == "waiting for an HEVC release"


def test_listed_non_hevc_is_taken_once_the_hevc_wait_has_passed():
    result = evaluate(_target(), _week_one(), _ts("2026-10-23 17:10"), Policy())

    assert result.best.torrent.name == SUBSPLEASE_ABS
    assert result.best.offset == 48
    assert _verdicts(result)[ASW_ABS] == WAITING


def test_hevc_from_a_listed_group_wins_when_available():
    torrents = _week_one() + [_torrent(ERAI_HEVC, "2026-10-23 19:12", 304, trusted = True)]
    result = evaluate(_target(), torrents, _ts("2026-10-23 19:30"), Policy())
    assert result.best.torrent.name == ERAI_HEVC


def test_hevc_is_taken_immediately_without_waiting():
    torrents = [_torrent(ERAI_HEVC, "2026-10-23 15:10", 10)]
    result = evaluate(_target(), torrents, AIRED + 45 * 60, Policy())
    assert result.best.torrent.name == ERAI_HEVC


def test_unlisted_groups_wait_for_the_fallback_window():
    target = _target(submitters = ("SubsPlease",))
    torrents = [_torrent(ERAI_HEVC, "2026-10-23 15:10", 300)]

    early = evaluate(target, torrents, DUE + 1 * HOUR, Policy())
    assert early.best is None
    assert early.waiting[0].reason == "waiting for SubsPlease"

    late = evaluate(target, torrents, DUE + 2 * HOUR + 1, Policy())
    assert late.best.torrent.name == ERAI_HEVC
    assert late.best.listed is False


def test_listed_group_beats_unlisted_hevc_after_fallback():
    target = _target(submitters = ("SubsPlease",))
    torrents = [
        _torrent(ASW_ABS, "2026-10-23 16:27", 900),
        _torrent(SUBSPLEASE_ABS, "2026-10-23 15:05", 50),
    ]
    result = evaluate(target, torrents, DUE + 3 * HOUR, Policy())
    assert result.best.torrent.name == SUBSPLEASE_ABS


def test_no_submitters_means_any_group_and_hevc_first():
    target = _target(submitters = ())
    torrents = [
        _torrent(ERAI_AVC_1080, "2026-10-23 15:00", 882),
        _torrent(ASW_ABS, "2026-10-23 16:27", 12),
    ]
    result = evaluate(target, torrents, DUE + 3 * HOUR, Policy())
    assert result.best.torrent.name == ASW_ABS


def test_without_hevc_preference_nothing_waits():
    result = evaluate(_target(), _week_one(), DUE, Policy(prefer_hevc = False))
    assert result.best.torrent.name == SUBSPLEASE_ABS
    assert not [c for c in result.waiting if c.listed]


def test_preferred_resolution_beats_seeders():
    target = _target(submitters = ("Erai-raws",))
    torrents = [
        _torrent(ERAI_AVC_480, "2026-10-23 15:01", 5000),
        _torrent(ERAI_AVC_720, "2026-10-23 15:01", 4000),
        _torrent(ERAI_AVC_1080, "2026-10-23 15:00", 3),
    ]
    result = evaluate(target, torrents, DUE + 3 * HOUR, Policy())
    assert [c.torrent.name for c in result.matches] == [ERAI_AVC_1080, ERAI_AVC_720, ERAI_AVC_480]

    result = evaluate(target, torrents, DUE + 3 * HOUR, Policy(resolution = 720))
    assert result.best.torrent.name == ERAI_AVC_720


def test_v2_is_preferred_over_v1():
    target = _target(submitters = ("SubsPlease",))
    v1 = "[SubsPlease] Kusuriya no Hitorigoto - 52 (1080p) [AAAAAAAA].mkv"
    v2 = "[SubsPlease] Kusuriya no Hitorigoto - 52v2 (1080p) [BBBBBBBB].mkv"
    torrents = [_torrent(v1, "2026-10-23 15:05", 900), _torrent(v2, "2026-10-23 18:00", 40)]
    result = evaluate(target, torrents, DUE + 6 * HOUR, Policy())
    assert result.best.torrent.name == v2


def test_absolute_number_without_prequel_data_is_a_near_miss():
    target = _target(prequel_counts = (), submitters = ("SubsPlease",))
    result = evaluate(target, [_torrent(SUBSPLEASE_ABS, "2026-10-23 15:05")], DUE + 3 * HOUR, Policy())

    assert result.best is None
    assert result.near[0].verdict == NEAR
    assert result.near[0].reason == "numbered episode 52; expected 4"
    assert result.near[0].listed is True


def test_approved_offset_makes_later_weeks_automatic():
    target = _target(prequel_counts = (), extra_offsets = frozenset({48}))
    result = evaluate(target, [_torrent(SUBSPLEASE_ABS, "2026-10-23 15:05")], DUE + 3 * HOUR, Policy())
    assert result.best.offset == 48


def test_learned_group_offset_is_enforced():
    target = _target(learned = {"subsplease": 48}, submitters = ("SubsPlease",))
    relative = "[SubsPlease] Kusuriya no Hitorigoto - 04 (1080p) [CCCCCCCC].mkv"
    torrents = [
        _torrent(relative, "2026-10-23 15:05"),
        _torrent(SUBSPLEASE_ABS, "2026-10-23 15:06"),
    ]
    result = evaluate(target, torrents, DUE + 3 * HOUR, Policy())
    assert result.best.torrent.name == SUBSPLEASE_ABS
    assert _verdicts(result)[relative] == NEAR


def test_wrong_season_label_this_week_is_a_near_miss_not_a_match():
    late_dub = "[Group] Kusuriya no Hitorigoto 2nd Season - 04 [1080p HEVC][Dual Audio]"
    result = evaluate(_target(submitters = ()), [_torrent(late_dub, "2026-10-24 10:00")], DUE + 30 * HOUR, Policy())
    assert result.best is None
    assert result.near[0].reason == "numbered S02E04; expected 4 or 28 or 52"


def test_continuous_numbering_under_an_earlier_season_label():
    target = _target(prequel_counts = (12, 12), submitters = ())
    tvdb_style = "[Group] Kusuriya no Hitorigoto - S02E16 [1080p HEVC]"
    result = evaluate(target, [_torrent(tvdb_style, "2026-10-23 16:00")], DUE, Policy())
    assert result.best.offset == 12


def test_alias_number_can_carry_the_match():
    target = _target(seasons = frozenset({9}), submitters = ())
    result = evaluate(target, [_torrent(HALF_BAKED, "2026-10-25 21:45")], DUE + 60 * HOUR, Policy())
    assert result.best.offset == 48


def test_offset_override_replaces_detection():
    target = _target(prequel_counts = (), offset_override = 48, submitters = ())
    relative_unmarked = "[Group] Kusuriya no Hitorigoto - 04 [1080p HEVC]"
    torrents = [
        _torrent(ASW_ABS, "2026-10-23 16:27"),
        _torrent(ERAI_HEVC, "2026-10-23 19:12"),
        _torrent(relative_unmarked, "2026-10-23 16:00"),
    ]
    verdicts = _verdicts(evaluate(target, torrents, DUE + 6 * HOUR, Policy()))
    assert verdicts[ASW_ABS] == MATCH
    assert verdicts[ERAI_HEVC] == MATCH
    assert verdicts[relative_unmarked] == NEAR
    assert accepted_numbers(target) == [4, 52]


@pytest.mark.parametrize("name,reason", [
    ("[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 01 ~ 12 [1080p][Batch]", "batch"),
    ("[Erai-raws] Kusuriya no Hitorigoto 3rd Season - 04.5 [1080p]", "special or extra, not a regular episode"),
    ("[Erai-raws] Maomao no Hitorigoto 3rd Season - 04 [1080p]", "different title"),
    ("[Erai-raws] Kusuriya no Hitorigoto 3rd Season [1080p]", "no episode number"),
])
def test_hard_rejections(name, reason):
    result = evaluate(_target(search_name = ""), [_torrent(name, "2026-10-23 15:00")], DUE + 6 * HOUR, Policy())
    assert [(c.verdict, c.reason) for c in result.rejected] == [(REJECTED, reason)]


def test_missing_magnet_is_rejected():
    t = _torrent(ERAI_HEVC, "2026-10-23 15:00")
    bare = Torrent(**{**t.__dict__, "magnet": ""})
    result = evaluate(_target(), [bare], DUE, Policy())
    assert result.rejected[0].reason == "no magnet link"


def test_nickname_search_name_matches_by_tokens():
    target = Target(
        episode = 4, airing_at = AIRED, due_at = DUE,
        names = ("As a Reincarnated Aristocrat, I'll Use My Appraisal Skill to Rise in the World Season 3",),
        search_name = "tensei kizoku s3",
        seasons = frozenset({3}),
        submitters = ("Erai-raws",),
    )
    name = "[Erai-raws] Tensei Kizoku, Kantei Skill de Nariagaru 3rd Season - 04 [1080p CR WEBRip HEVC AAC]"
    result = evaluate(target, [_torrent(name, "2026-10-23 16:00")], DUE, Policy())
    assert result.best is not None


def test_unknown_upload_time_is_not_treated_as_old():
    result = evaluate(_target(), [_torrent(ERAI_HEVC, None)], DUE, Policy())
    assert result.best is not None


def test_unknown_air_time_skips_date_and_wait_rules():
    target = _target(airing_at = None, due_at = None, submitters = ("SubsPlease",))
    result = evaluate(target, [_torrent(ERAI_AVC_1080, "2020-01-01 00:00")], DUE, Policy())
    assert result.best is not None


def test_same_torrent_from_two_queries_is_counted_once():
    t = _torrent(ERAI_HEVC, "2026-10-23 19:12")
    result = evaluate(_target(), [t, t], DUE + 6 * HOUR, Policy())
    assert len(result.ordered()) == 1


def test_submitter_matches_exact_group_not_substring():
    target = _target(submitters = ("SubsPlease",))
    reencode = "[ASW] Kusuriya no Hitorigoto - 52 [1080p HEVC] (SubsPlease source)"
    result = evaluate(target, [_torrent(reencode, "2026-10-23 16:27")], DUE, Policy())
    assert result.waiting[0].listed is False


def test_summary_describes_each_outcome():
    assert evaluate(_target(), [], DUE, Policy()).summary() == "nothing released yet"
    waiting = evaluate(_target(), _week_one(), DUE, Policy())
    assert waiting.summary() == "waiting for an HEVC release"


def test_helpers():
    assert cumulative_offsets((24, 24)) == [0, 24, 48]
    assert accepted_numbers(_target()) == [4, 28, 52]
    assert expected_seasons(NAMES, [24, 24]) == frozenset({3})
    assert expected_seasons(("Show",), None) == frozenset({1})
    assert expected_seasons(("Show: Final Arc",), [12, 12]) == frozenset({3})
    assert group_key("Erai-raws") == "erairaws"
