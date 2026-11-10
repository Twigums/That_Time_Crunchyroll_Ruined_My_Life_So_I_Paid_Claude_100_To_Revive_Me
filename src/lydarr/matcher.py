"""Decide which torrent, if any, is a given episode of a given show.

A torrent is only ever picked when it clears every hard gate: uploaded after
the episode aired, same series, a single regular episode, and an episode
number that makes sense for this show (season-relative, or continuing from
earlier seasons). Among those, timing rules decide whether to take one now or
keep waiting for a listed submitter or an HEVC encode. Torrents that look like
the right show at the right time but carry an unexpected number are reported
as near misses so a person can confirm them.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from lydarr.release import Release, mentions_special, normalize, parse_release, split_season
from nyaa.types import Torrent, TorrentType

EARLY_TOLERANCE = 24 * 3600
TITLE_THRESHOLD = 80

MATCH = "match"
WAITING = "waiting"
NEAR = "near"
REJECTED = "rejected"


@dataclass(frozen = True)
class Policy:
    fallback_after: float = 2 * 3600
    hevc_wait: float = 2 * 3600
    prefer_hevc: bool = True
    resolution: int = 1080


@dataclass(frozen = True)
class Target:
    episode: int
    airing_at: int | None = None
    next_airing_at: int | None = None
    due_at: float | None = None
    names: tuple[str, ...] = ()
    search_name: str = ""
    seasons: frozenset[int] = frozenset({1})
    prequel_counts: tuple[int, ...] = ()
    offset_override: int = 0
    learned: Mapping[str, int] = field(default_factory = dict)
    extra_offsets: frozenset[int] = frozenset()
    submitters: tuple[str, ...] = ()
    check_dates: bool = True


@dataclass
class Candidate:
    torrent: Torrent
    release: Release
    verdict: str = REJECTED
    reason: str = ""
    listed: bool = False
    submitter_index: int = 0
    offset: int | None = None


@dataclass
class MatchResult:
    best: Candidate | None = None
    matches: list[Candidate] = field(default_factory = list)
    waiting: list[Candidate] = field(default_factory = list)
    near: list[Candidate] = field(default_factory = list)
    rejected: list[Candidate] = field(default_factory = list)

    def ordered(self) -> list[Candidate]:
        return [*self.matches, *self.waiting, *self.near, *self.rejected]

    def summary(self) -> str:
        if self.best:
            return f"picked {self.best.torrent.name}"
        if self.waiting:
            return self.waiting[0].reason
        if self.near:
            return f"{len(self.near)} possible release(s) need a look"
        return "nothing released yet"


def group_key(name: str) -> str:
    return "".join(c for c in normalize(name) if c.isalnum())


def expected_seasons(names: tuple[str, ...] | list[str], prequel_counts: list[int] | tuple[int, ...] | None) -> frozenset[int]:
    """Season numbers a release may plausibly be labelled with."""
    seasons = {season for _, season in map(split_season, names) if season}
    if prequel_counts is not None:
        seasons.add(len(prequel_counts) + 1)
    return frozenset(seasons or {1})


def cumulative_offsets(prequel_counts: tuple[int, ...] | list[int]) -> list[int]:
    """0, then the running total of earlier seasons' lengths, nearest season first."""
    out, total = [0], 0
    for count in prequel_counts:
        total += count
        out.append(total)
    return out


def accepted_numbers(target: Target) -> list[int]:
    """Every episode number a release of this episode may carry."""
    if target.offset_override:
        offsets = {0, target.offset_override}
    else:
        offsets = set(cumulative_offsets(target.prequel_counts))
    offsets |= set(target.extra_offsets) | set(target.learned.values())
    return sorted({target.episode + off for off in offsets if target.episode + off > 0})


def _known_titles(target: Target) -> set[str]:
    return {key for key in (normalize(split_season(n)[0]) for n in target.names if n) if key}


def _title_ok(release: Release, target: Target) -> bool:
    known = _known_titles(target)
    for title in release.titles:
        key = normalize(title)
        if not key:
            continue
        if key in known or any(fuzz.ratio(key, k) >= TITLE_THRESHOLD for k in known):
            return True
    if target.search_name:
        wanted = set(normalize(split_season(target.search_name)[0]).split())
        have = set(normalize(release.raw).split())
        if wanted and wanted <= have:
            return True
    return False


def _offset_for(release: Release, target: Target) -> int | None:
    """The numbering offset under which this release is the target episode, if any."""
    n = target.episode
    learned = target.learned.get(group_key(release.group)) if release.group else None
    cumulative = cumulative_offsets(target.prequel_counts)
    extra = set(target.extra_offsets)

    numbers: list[tuple[int, int | None]] = []
    if release.episode is not None:
        numbers.append((release.episode, release.season))
    numbers.extend((alt, None) for alt in release.alt_episodes)

    for number, season in numbers:
        offset = number - n
        if learned is not None:
            if offset == learned:
                return offset
            continue
        if offset in extra:
            return offset
        if season is not None and season in target.seasons and offset == 0:
            return 0
        if target.offset_override:
            if offset == target.offset_override:
                return offset
            continue
        if season is None:
            if offset in cumulative:
                return offset
        elif season in target.seasons:
            if offset in cumulative:
                return offset
        elif season < max(target.seasons):
            if offset in cumulative[1:]:
                return offset
    return None


def in_episode_window(target: Target, published: int | None) -> bool:
    """False once an upload is recent enough to belong to the following episode instead."""
    if published is None or target.next_airing_at is None:
        return True
    return published < target.next_airing_at - EARLY_TOLERANCE


def fits(release: Release, target: Target) -> int | None:
    """Numbering offset if this release could be the target episode, ignoring dates and timing."""
    if release.batch or release.episode is None or not _title_ok(release, target):
        return None
    return _offset_for(release, target)


def _listed_index(release: Release, submitters: tuple[str, ...]) -> int | None:
    wanted = [group_key(s) for s in submitters]
    group = group_key(release.group)
    tags = {group_key(t) for t in release.tags}
    for index, key in enumerate(wanted):
        if not key:
            continue
        if key == group or key in tags:
            return index
        if not group and key in group_key(release.raw):
            return index
    return None


def _number_note(release: Release, target: Target) -> str:
    seen = f"S{release.season:02d}E{release.episode:02d}" if release.season else f"episode {release.episode}"
    wanted = " or ".join(str(n) for n in accepted_numbers(target))
    return f"numbered {seen}; expected {wanted}"


def _resolution_rank(release: Release, preferred: int) -> tuple[int, int]:
    if release.resolution is None:
        return (1, 0)
    if release.resolution == preferred:
        return (0, 0)
    if release.resolution > preferred:
        return (2, release.resolution - preferred)
    return (3, preferred - release.resolution)


def _rank(c: Candidate, policy: Policy) -> tuple:
    hevc_rank = 0 if (c.release.hevc or not policy.prefer_hevc) else 1
    type_rank = 0 if c.torrent.type == TorrentType.TRUSTED else 1
    return (
        0 if c.listed else 1,
        hevc_rank,
        _resolution_rank(c.release, policy.resolution),
        c.submitter_index,
        -c.release.version,
        type_rank,
        -c.torrent.seeders,
    )


def evaluate(target: Target, torrents: list[Torrent], now: float, policy: Policy) -> MatchResult:
    result = MatchResult()
    special_ok = any(mentions_special(n) for n in target.names)
    seen_hashes: set[str] = set()

    for torrent in torrents:
        key = torrent.info_hash or torrent.id
        if key in seen_hashes:
            continue
        seen_hashes.add(key)

        release = parse_release(torrent.name)
        index = _listed_index(release, target.submitters)
        c = Candidate(
            torrent = torrent,
            release = release,
            listed = index is not None or not target.submitters,
            submitter_index = index if index is not None else len(target.submitters),
        )
        published = torrent.published
        if not torrent.magnet:
            c.reason = "no magnet link"
        elif (target.check_dates and target.airing_at is not None and published is not None
              and published < target.airing_at - EARLY_TOLERANCE):
            c.reason = "uploaded before this episode aired"
        elif not _title_ok(release, target):
            c.reason = "different title"
        elif release.batch:
            c.reason = "batch"
        elif release.special and not special_ok:
            c.reason = "special or extra, not a regular episode"
        elif release.episode is None:
            c.reason = "no episode number"
        else:
            offset = _offset_for(release, target)
            if offset is None and not in_episode_window(target, published):
                c.reason = "a later episode"
            elif offset is None:
                c.verdict = NEAR
                c.reason = _number_note(release, target)
            else:
                c.offset = offset
                _apply_timing(c, target, now, policy, published)

        if c.verdict == MATCH:
            result.matches.append(c)
        elif c.verdict == WAITING:
            result.waiting.append(c)
        elif c.verdict == NEAR:
            result.near.append(c)
        else:
            result.rejected.append(c)

    result.matches.sort(key = lambda c: _rank(c, policy))
    result.waiting.sort(key = lambda c: _rank(c, policy))
    result.near.sort(key = lambda c: (0 if c.listed else 1, -c.torrent.seeders))
    result.best = result.matches[0] if result.matches else None
    return result


def _apply_timing(c: Candidate, target: Target, now: float, policy: Policy, published: int | None) -> None:
    due = target.due_at

    if not c.listed and due is not None and now < due + policy.fallback_after:
        c.verdict = WAITING
        c.reason = "waiting for " + ", ".join(target.submitters)
        return
    if policy.prefer_hevc and not c.release.hevc and due is not None:
        origin = min(published, due) if published is not None else due
        if now < origin + policy.hevc_wait:
            c.verdict = WAITING
            c.reason = "waiting for an HEVC release"
            return
    c.verdict = MATCH
    c.reason = "matches" if c.offset == 0 else f"matches (numbered +{c.offset})"
