"""Shared helpers for the nyaa parser sub-package."""
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_BTIH = re.compile(r"btih:([0-9A-Fa-f]{40}|[A-Za-z2-7]{32})")


def last_segment(text: str) -> str:
    return text.rstrip("/").rsplit("/", 1)[-1]


def read_int(text: str) -> int:
    try:
        return int(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def parse_timestamp(text: str) -> int | None:
    """Unix time of an RSS pubDate or an HTML listing date; both are UTC."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        # Nyaa writes "-0000", which the parser reads as "zone unknown" and returns naive.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo = timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo = timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def magnet_hash(magnet: str) -> str:
    m = _BTIH.search(magnet or "")
    return m.group(1).lower() if m else ""
