"""Command line entry point: run lydarr, or check what it would do."""
import argparse
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from lydarr.config import AppConfig, load_config
from lydarr.file_manager import MediaEntry, MediaState, WatchlistError, read_entries
from lydarr.media_store import MediaStore
from lydarr.paths import resolve_download_dir
from lydarr.state import TrackerState
from lydarr.tracker import Tracker

_logger = logging.getLogger("lydarr")

_LOG_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _setup_logging(log_file: str | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt = "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(RotatingFileHandler(
            log_file, maxBytes = _LOG_BYTES, backupCount = _LOG_BACKUPS, encoding = "utf-8",
        ))
    # uvicorn's own messages (startup, unhandled errors) go to the same places.
    for name, level in (("lydarr", logging.INFO), ("uvicorn.error", logging.INFO)):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False
        for handler in handlers:
            handler.setFormatter(fmt)
            logger.addHandler(handler)


def _services(cfg: AppConfig, entries: list[MediaEntry]) -> tuple[MediaState, MediaStore, TrackerState, Tracker]:
    state = MediaState(entries)
    store = MediaStore(cfg.cache_file, state, cfg.anime_file)
    store.load()
    tstate = TrackerState(cfg.state_file)
    tstate.load()
    return state, store, tstate, Tracker(cfg, state, store, tstate)


async def _serve(cfg: AppConfig, host: str, port: int, web: bool) -> None:
    entries = read_entries(cfg.anime_file)
    state, store, tstate, tracker = _services(cfg, entries)
    _logger.info("Watchlist: %s (%d entries). Downloads go under %s unless an entry sets its own path.",
                 cfg.anime_file, len(entries), cfg.default_dir)

    if not web:
        _logger.info("Running without the web UI.")
        await asyncio.gather(tracker.run(), tracker.housekeeping())
        return

    import uvicorn
    from lydarr.web.app import create_app

    if host not in _LOOPBACK and cfg.lydarr_user is None:
        _logger.warning(
            "The web UI is reachable from other machines (%s) without a password. "
            "Set LYDARR_USER and LYDARR_PASS, or bind with --host 127.0.0.1.", host,
        )
    if not tstate.enabled:
        _logger.info("Tracker is off. Press Start in the web UI; lydarr remembers it across restarts.")
    app = create_app(cfg, state, store = store, tstate = tstate, tracker = tracker, resume_tracker = True)
    server = uvicorn.Server(uvicorn.Config(app, host = host, port = port, log_config = None))
    await server.serve()


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%a %Y-%m-%d %H:%M %Z")


async def _check(cfg: AppConfig, title: str | None, episode: int | None) -> int:
    """Print what the tracker knows and would pick, without changing any file."""
    entries = [e for e in read_entries(cfg.anime_file) if e.media_type != "manga" and not e.deprecated]
    if title:
        entries = [e for e in entries if title.lower() in e.title.lower()]
    if not entries:
        print("No matching active anime entries.")
        return 1

    with tempfile.TemporaryDirectory(prefix = "lydarr-check-") as scratch:
        # Work on copies, so a check never writes to the watchlist or the live cache.
        scratch_cfg = AppConfig(**{**cfg.__dict__, "anime_file": os.path.join(scratch, "anime.toml"),
                                   "cache_file": os.path.join(scratch, "cache.json")})
        if os.path.exists(cfg.cache_file):
            shutil.copy(cfg.cache_file, scratch_cfg.cache_file)
        state = MediaState(entries)
        store = MediaStore(scratch_cfg.cache_file, state, scratch_cfg.anime_file)
        store.load()
        tstate = TrackerState(cfg.state_file)
        tstate.load()
        tracker = Tracker(cfg, state, store, tstate)

        if not await store.refresh():
            print(f"! AniList: {store.status()['error']} (showing cached data)\n")

        now = time.time()
        for entry in state.entries():
            info = store.info(entry)
            print(entry.title)
            if info is None:
                print("    not found on AniList\n")
                continue
            print(f"    AniList   #{info.id} {info.display_title()} ({info.status.value.lower()})")
            print(f"    saves to  {resolve_download_dir(cfg, entry, info)}")
            upcoming = store.next_episode(entry, now)
            if upcoming:
                print(f"    next      ep {upcoming[0]:02d} airs {_local(upcoming[1])}")
            aired = [ep for ep, at in store.episodes(entry) if at <= now]
            target = episode or (aired[-1] if aired else None)
            if target is None:
                print("    no aired episode to check yet\n")
                continue
            try:
                result = await tracker.preview(entry, target)
            except Exception as exc:
                print(f"    ep {target:02d}: Nyaa search failed ({exc})\n")
                continue
            print(f"    ep {target:02d}: {result.summary()}")
            shown = [c for c in result.ordered() if c.verdict != "rejected"][:8]
            for c in shown:
                print(f"      {c.verdict:8} {c.torrent.seeders:>5} seeds  {c.reason}")
                print(f"               {c.torrent.name}")
            rejected = len(result.rejected)
            if rejected:
                reasons: dict[str, int] = {}
                for c in result.rejected:
                    reasons[c.reason] = reasons.get(c.reason, 0) + 1
                detail = ", ".join(f"{n} {reason}" for reason, n in sorted(reasons.items(), key = lambda kv: -kv[1]))
                print(f"      ignored  {rejected} other result(s): {detail}")
            print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog = "lydarr",
        description = "Track airing anime and add each new episode to Transmission.",
    )
    parser.add_argument("--host", default = None,
                        help = "web UI bind address (default: 0.0.0.0, or $LYDARR_WEB_HOST)")
    parser.add_argument("--port", type = int, default = None,
                        help = "web UI port (default: 8080, or $LYDARR_WEB_PORT)")
    parser.add_argument("--no-web", action = "store_true",
                        help = "run the tracker without the web UI")
    parser.add_argument("--web-only", action = "store_true", help = argparse.SUPPRESS)
    parser.add_argument("--log", metavar = "FILE", default = None,
                        help = "also write logs to FILE, rotated at 5 MB (or $LYDARR_LOG_FILE)")
    parser.add_argument("--debug", action = "store_true", help = argparse.SUPPRESS)

    commands = parser.add_subparsers(dest = "command")
    check = commands.add_parser(
        "check", help = "show what the tracker knows and would download, without changing anything",
    )
    check.add_argument("title", nargs = "?", help = "only entries whose title contains this text")
    check.add_argument("--episode", type = int, default = None, help = "episode to check (default: latest aired)")

    args = parser.parse_args()
    cfg = load_config()

    try:
        if args.command == "check" or args.debug:
            title = getattr(args, "title", None)
            episode = getattr(args, "episode", None)
            sys.exit(asyncio.run(_check(cfg, title, episode)))

        host = args.host if args.host is not None else os.environ.get("LYDARR_WEB_HOST", "0.0.0.0")
        port = args.port if args.port is not None else int(os.environ.get("LYDARR_WEB_PORT", "8080"))
        _setup_logging(args.log or cfg.log_file)
        asyncio.run(_serve(cfg, host, port, web = not args.no_web))
    except WatchlistError as exc:
        print(f"lydarr: {exc}", file = sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
