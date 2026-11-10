"""Parse torrent release names into structured fields.

Release groups have no common naming scheme, so this recognises the handful of
shapes that cover nearly everything on Nyaa:

    [Group] Title - 04 (1080p) [CRC].mkv
    [Group] Title 3rd Season - 04 [1080p HEVC]
    [Group] Title S2 - 04v2 [720p]
    [Group] Title - S03E04 [1080p][HEVC x265 10bit]
    Title S03E04 Episode Name 1080p WEB-DL H.264-GROUP
    [Group] English Title - S03E04 [..] | Romaji Title - 52 | ..
"""
import re
import unicodedata
from dataclasses import dataclass

_EXTENSION = re.compile(r"\.(mkv|mp4|avi|ts|m2ts|webm)\s*$", re.I)
_LEAD_GROUP = re.compile(r"^\s*(?:\[([^\]]+)\]|【([^】]+)】)\s*")
_SQUARE = re.compile(r"\[[^\]]*\]|【[^】]*】")
_PAREN = re.compile(r"\(([^()]*)\)")
_RESOLUTION = re.compile(r"(?<![0-9A-Za-z])(\d{3,4})[pP](?![A-Za-z])|(?<!\d)\d{3,4}\s?[xX×]\s?(\d{3,4})(?!\d)")
_UHD = re.compile(r"(?<![0-9A-Za-z])(4K|UHD)(?![0-9A-Za-z])", re.I)
_HEVC = re.compile(r"hevc|[xh][. ]?265", re.I)
_BATCH_WORD = re.compile(r"(?<![A-Za-z])(batch|complete|season pack|bd[- ]?box)(?![A-Za-z])", re.I)
_SPECIAL_WORD = re.compile(
    r"(?<![A-Za-z])(OVA|OAD|OAV|Movie|Gekijouban|Specials?|SP\d*|Recap|NCOP\d*|NCED\d*|PV\d*|"
    r"Trailer|Preview|Teaser|Extras?|Menu|CM\d*|Creditless)(?![A-Za-z])",
    re.I,
)
_PAREN_TAG = re.compile(
    r"\d{3,4}p|(?<![A-Za-z])(hevc|x26[45]|h\.?26[45]|av1|aac|flac|opus|e-?ac-?3|ddp?|web|bd|blu-?ray|dvd|"
    r"batch|weekly|multi|dual|subs?|dub|eng|uncensored|remux|10.?bit|8.?bit|hi10p?|vostfr|raw)(?![A-Za-z])|"
    r"^[0-9A-Fa-f]{8}$|^\d{4}$",
    re.I,
)
_PAREN_SEASON = re.compile(r"^\s*(?:season|s)\s*(\d{1,2})\s*$", re.I)
_PAREN_RANGE = re.compile(r"^\s*(?:e|ep)?\s*\d{1,4}\s*[-~]\s*(?:e|ep)?\s*\d{1,4}\s*$", re.I)

_SXXEYY = re.compile(
    r"(?<![0-9A-Za-z])S(\d{1,2})[ ._]?E(\d{1,4})(?:\s?[-~]\s?E?(\d{1,4}))?(?:v(\d))?(?![0-9A-Za-z])", re.I,
)
_DASH_RANGE = re.compile(r"\s-\s(\d{1,4})\s?[-~]\s?(\d{1,4})(?=\s|$)")
_DASH_NUMBER = re.compile(r"\s-\s(\d{1,4})(\.\d)?(?:v(\d))?(?=\s|$)")
_DASH_SPECIAL = re.compile(r"\s-\s(?:OVA|OAD|SP|NCOP|NCED|PV|Special|Movie|Preview)\s?\d*(?=\s|$)", re.I)
_EPISODE_WORD = re.compile(r"(?<![A-Za-z])(?:Episode|Ep\.?|E)\s?(\d{1,4})(?:v(\d))?(?![0-9A-Za-z])", re.I)
_HASH_NUMBER = re.compile(r"#(\d{1,4})(?!\d)")
_BARE_NUMBER = re.compile(r"(?<![0-9A-Za-z.])(\d{1,4})(?:v(\d))?(?![0-9A-Za-z]|\.\d)")
_NOT_AN_EPISODE_BEFORE = re.compile(r"(?:season|part|cour|vol|volume|no|s)\.?\s*$", re.I)

_SEASON_IN_TITLE = (
    re.compile(r"(?<![0-9A-Za-z])(\d{1,2})(?:st|nd|rd|th)\s+Season(?![A-Za-z])", re.I),
    re.compile(r"(?<![A-Za-z])Season\s+(\d{1,2})(?![0-9A-Za-z])", re.I),
    re.compile(r"(?<![0-9A-Za-z])S(\d{1,2})(?![0-9A-Za-z])", re.I),
)
_ROMAN_SUFFIX = re.compile(r"\s(II|III|IV)\s*$")
_ROMAN = {"II": 2, "III": 3, "IV": 4}
_SCENE_GROUP = re.compile(r"(?<=[0-9A-Za-z])-([A-Za-z0-9]{2,})\s*$")
_TRIM = " \t-_.,~:|"


@dataclass(frozen = True)
class Release:
    raw: str
    group: str = ""
    titles: tuple[str, ...] = ()
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    alt_episodes: tuple[int, ...] = ()
    version: int = 1
    resolution: int | None = None
    hevc: bool = False
    batch: bool = False
    special: bool = False
    tags: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else ""


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    folded = unicodedata.normalize("NFKD", text or "")
    kept = "".join(c if c.isalnum() else " " for c in folded if not unicodedata.combining(c))
    return " ".join(kept.lower().split())


def mentions_special(text: str) -> bool:
    return bool(_SPECIAL_WORD.search(text or ""))


def split_season(title: str) -> tuple[str, int | None]:
    """Remove a season marker from a title, returning the bare title and the season."""
    for pattern in _SEASON_IN_TITLE:
        m = pattern.search(title)
        if m:
            return (title[:m.start()] + " " + title[m.end():]).strip(_TRIM), int(m.group(1))
    m = _ROMAN_SUFFIX.search(title)
    if m:
        return title[:m.start()].strip(_TRIM), _ROMAN[m.group(1)]
    return title.strip(_TRIM), None


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _year_like(number: int) -> bool:
    return 1900 <= number <= 2099


@dataclass
class _Segment:
    title: str = ""
    aliases: tuple[str, ...] = ()
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    version: int = 1
    special: bool = False
    batch: bool = False


def _find_marker(text: str) -> tuple[int, dict] | None:
    """Locate the episode marker; returns its start offset and what it says."""
    m = _SXXEYY.search(text)
    if m:
        return m.start(), {
            "season": int(m.group(1)), "episode": int(m.group(2)),
            "episode_end": int(m.group(3)) if m.group(3) else None,
            "version": int(m.group(4)) if m.group(4) else 1,
        }
    m = _DASH_RANGE.search(text)
    if m and int(m.group(2)) > int(m.group(1)):
        return m.start(), {"episode": int(m.group(1)), "episode_end": int(m.group(2))}
    dashes = list(_DASH_NUMBER.finditer(text))
    if dashes:
        m = dashes[-1]
        return m.start(), {
            "episode": int(m.group(1)), "special": bool(m.group(2)),
            "version": int(m.group(3)) if m.group(3) else 1,
        }
    m = _DASH_SPECIAL.search(text)
    if m:
        return m.start(), {"special": True}
    m = _EPISODE_WORD.search(text)
    if m:
        return m.start(), {"episode": int(m.group(1)), "version": int(m.group(2)) if m.group(2) else 1}
    m = _HASH_NUMBER.search(text)
    if m:
        return m.start(), {"episode": int(m.group(1))}
    for m in reversed(list(_BARE_NUMBER.finditer(text))):
        number = int(m.group(1))
        before = text[:m.start()]
        if not before.strip() or _year_like(number) or _NOT_AN_EPISODE_BEFORE.search(before):
            continue
        return m.start(), {"episode": number, "version": int(m.group(2)) if m.group(2) else 1}
    return None


def _parse_segment(text: str) -> _Segment:
    seg = _Segment()
    work = _SQUARE.sub(lambda m: " " * len(m.group(0)), text)
    aliases: list[str] = []
    for m in list(_PAREN.finditer(work)):
        inner = m.group(1)
        season = _PAREN_SEASON.match(inner)
        if season:
            seg.season = int(season.group(1))
        elif _PAREN_RANGE.match(inner):
            seg.batch = True
        elif not _PAREN_TAG.search(inner) and len(normalize(inner)) >= 3:
            aliases.append(inner.strip())
        work = _blank(work, m.start(), m.end())
    if " " not in work.strip() and work.count(".") >= 2:
        work = work.replace(".", " ")
    work = work.replace("_", " ")

    cut = len(work)
    for tag in (_RESOLUTION.search(work), _HEVC.search(work)):
        if tag:
            cut = min(cut, tag.start())
    marker = _find_marker(work[:cut])
    if marker is None:
        raw_title = work[:cut]
    else:
        start, found = marker
        raw_title = work[:start]
        if found.get("season") is not None:
            seg.season = found["season"]
        seg.episode = found.get("episode")
        seg.episode_end = found.get("episode_end")
        seg.version = found.get("version", 1)
        seg.special = bool(found.get("special"))
        if seg.episode_end is not None:
            seg.batch = True

    title, title_season = split_season(raw_title.strip(_TRIM))
    if seg.season is None:
        seg.season = title_season
    if _SPECIAL_WORD.search(title):
        seg.special = True
    seg.title = " ".join(title.split())
    seg.aliases = tuple(split_season(a)[0] for a in aliases)
    return seg


def parse_release(name: str) -> Release:
    raw = name or ""
    text = _EXTENSION.sub("", raw)
    group = ""
    lead = _LEAD_GROUP.match(text)
    if lead:
        group = (lead.group(1) or lead.group(2)).strip()
        text = text[lead.end():]

    parts = [p for p in re.split(r"\s+\|\s+", text) if p.strip()]
    main = _parse_segment(parts[0]) if parts else _Segment()

    titles: list[str] = [main.title, *main.aliases]
    alt_episodes: list[int] = []
    for part in parts[1:]:
        alias = _parse_segment(part)
        titles.extend([alias.title, *alias.aliases])
        if alias.episode is not None and not alias.batch and alias.episode != main.episode:
            alt_episodes.append(alias.episode)

    resolution = None
    res = _RESOLUTION.search(raw)
    if res:
        resolution = int(res.group(1) or res.group(2))
    elif _UHD.search(raw):
        resolution = 2160

    if not group and parts and (main.season is not None or res):
        tail = _SQUARE.sub(" ", _PAREN.sub(" ", parts[0])).strip()
        scene = _SCENE_GROUP.search(tail)
        if scene:
            group = scene.group(1)

    seen: set[str] = set()
    unique_titles: list[str] = []
    for t in titles:
        key = normalize(t)
        if key and key not in seen:
            seen.add(key)
            unique_titles.append(t)

    tags = tuple(normalize(m.group(0)) for m in _SQUARE.finditer(raw))
    return Release(
        raw = raw,
        group = group,
        titles = tuple(unique_titles),
        season = main.season,
        episode = main.episode,
        episode_end = main.episode_end,
        alt_episodes = tuple(dict.fromkeys(alt_episodes)),
        version = main.version,
        resolution = resolution,
        hevc = bool(_HEVC.search(raw)),
        batch = main.batch or bool(_BATCH_WORD.search(raw)),
        special = main.special,
        tags = tags,
    )
