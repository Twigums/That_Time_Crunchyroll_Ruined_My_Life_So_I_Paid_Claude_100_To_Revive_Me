"""Rate-limit-aware HTTP client for the AniList GraphQL API.

Every request goes through one gate: calls are serialised and spaced out, a 429
pauses all callers until AniList says to resume, and repeated outages open a
circuit breaker so background work stops hammering a server that is down.
"""
import asyncio
import logging
import time

import httpx

from anilist.types import AnilistError, AnilistRateLimited, AnilistUnavailable

_URL = "https://graphql.anilist.co"

_logger = logging.getLogger("lydarr.anilist")

_DEFAULT_INTERVAL = 0.7
_MAX_INLINE_WAIT = 5.0
_BREAKER_BASE = 60.0
_BREAKER_MAX = 1800.0
_FALLBACK_RETRY_AFTER = 60.0


class _Gate:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self.interval = _DEFAULT_INTERVAL
        self.next_allowed = 0.0
        self.blocked_until = 0.0
        self.rate_limited = False
        self.reason = ""
        self.failures = 0
        self.requests = 0

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._loop = loop
            self._lock = asyncio.Lock()
        return self._lock

    def block(self, seconds: float, reason: str, rate_limited: bool) -> None:
        self.blocked_until = time.monotonic() + seconds
        self.reason = reason
        self.rate_limited = rate_limited

    def fail(self, reason: str) -> float:
        self.failures += 1
        pause = min(_BREAKER_BASE * 2 ** (self.failures - 1), _BREAKER_MAX)
        self.block(pause, reason, rate_limited = False)
        return pause

    def succeed(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0
        self.rate_limited = False
        self.reason = ""

    def blocked_for(self) -> float:
        return max(0.0, self.blocked_until - time.monotonic())


_gate = _Gate()


def reset_gate() -> None:
    global _gate
    _gate = _Gate()


def gate_status() -> dict:
    return {
        "blocked_for": round(_gate.blocked_for()),
        "rate_limited": _gate.rate_limited and _gate.blocked_for() > 0,
        "reason": _gate.reason if _gate.blocked_for() > 0 else "",
        "requests": _gate.requests,
    }


def _header_number(headers, name: str) -> float | None:
    try:
        return float(headers.get(name, ""))
    except (TypeError, ValueError):
        return None


def _retry_after(headers) -> float:
    seconds = _header_number(headers, "Retry-After")
    if seconds is not None and seconds > 0:
        return seconds
    reset = _header_number(headers, "X-RateLimit-Reset")
    if reset is not None and reset > time.time():
        return reset - time.time()
    return _FALLBACK_RETRY_AFTER


def _error_message(r: httpx.Response) -> str:
    try:
        errors = r.json().get("errors") or []
        if errors and errors[0].get("message"):
            return str(errors[0]["message"])
    except Exception:
        pass
    lines = r.text.strip().splitlines()
    return lines[0][:160] if lines else "no response body"


def _note_limits(headers) -> None:
    limit = _header_number(headers, "X-RateLimit-Limit")
    if limit and limit > 0:
        _gate.interval = max(_DEFAULT_INTERVAL, 60.0 / limit * 1.05)


async def graphql(query: str, variables: dict, *, interactive: bool = False) -> dict:
    """Run one GraphQL request.

    `interactive` marks a request a person is waiting on: it may probe through
    an open circuit breaker, but never through a rate-limit window.
    """
    async with _gate.lock():
        wait = _gate.blocked_for()
        if wait > 0:
            if _gate.rate_limited and wait <= _MAX_INLINE_WAIT:
                await asyncio.sleep(wait)
            elif _gate.rate_limited:
                raise AnilistRateLimited(f"AniList rate limit reached; retry in {round(wait)}s", wait)
            elif not interactive:
                raise AnilistUnavailable(f"AniList unavailable ({_gate.reason}); retry in {round(wait)}s", wait)

        pause = _gate.next_allowed - time.monotonic()
        if pause > 0:
            await asyncio.sleep(pause)

        _gate.requests += 1
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(_URL, json = {"query": query, "variables": variables}, timeout = 15)
        except httpx.HTTPError as exc:
            reason = f"{type(exc).__name__}: {exc}".rstrip(": ")
            pause = _gate.fail(reason)
            raise AnilistUnavailable(f"AniList unreachable ({reason})", pause) from exc
        finally:
            _gate.next_allowed = time.monotonic() + _gate.interval

        _note_limits(r.headers)

        if r.status_code == 429:
            pause = _retry_after(r.headers)
            _gate.block(pause, "rate limited", rate_limited = True)
            _logger.warning("AniList rate limit hit; pausing all requests for %ds.", round(pause))
            raise AnilistRateLimited(f"AniList rate limit reached; retry in {round(pause)}s", pause)

        if r.status_code != 200:
            message = _error_message(r)
            if r.status_code >= 500 or r.status_code == 403:
                pause = _gate.fail(message)
                raise AnilistUnavailable(f"HTTP {r.status_code}: {message}", pause)
            raise AnilistError(f"HTTP {r.status_code}: {message}")

        data = r.json()
        if data.get("errors"):
            raise AnilistError(data["errors"][0].get("message", str(data["errors"])))
        _gate.succeed()
        return data["data"]
