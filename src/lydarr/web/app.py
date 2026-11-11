"""FastAPI application factory."""
import asyncio
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from anilist.types import AnilistError
from lydarr.config import AppConfig
from lydarr.file_manager import MediaState
from lydarr.media_store import MediaStore
from lydarr.state import TrackerState
from lydarr.tracker import Tracker
from nyaa.types import NyaaError

_logger = logging.getLogger("lydarr.web")

_security = HTTPBasic(auto_error = False)


def _make_auth_dependency(cfg: AppConfig) -> Callable[..., None]:
    def require_auth(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ) -> None:
        if cfg.lydarr_user is None:
            return
        if credentials is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Authentication required",
                headers = {"WWW-Authenticate": "Basic"},
            )
        user_ok = secrets.compare_digest(credentials.username.encode(), cfg.lydarr_user.encode())
        pass_ok = secrets.compare_digest(
            credentials.password.encode(),
            (cfg.lydarr_pass or "").encode(),
        )
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail = "Invalid credentials",
                headers = {"WWW-Authenticate": "Basic"},
            )
    return require_auth


def create_app(
    cfg: AppConfig,
    state: MediaState,
    *,
    store: MediaStore | None = None,
    tstate: TrackerState | None = None,
    tracker: Tracker | None = None,
    resume_tracker: bool = False,
) -> FastAPI:
    """Build the web app.

    `resume_tracker` starts the tracker on boot when it was running at the last
    shutdown, so a restart or reboot does not silently stop downloads.
    """
    if store is None:
        store = MediaStore(cfg.cache_file, state, cfg.anime_file)
        store.load()
    if tstate is None:
        tstate = TrackerState(cfg.state_file)
        tstate.load()
    if tracker is None:
        tracker = Tracker(cfg, state, store, tstate)

    from lydarr.web.routes import activity, anime, daemon, torrents

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cfg = cfg
        app.state.anime_state = state
        app.state.store = store
        app.state.tstate = tstate
        app.state.tracker = tracker
        app.state.daemon_task = None
        app.state.daemon_started_at = None
        app.state.search_cache = {}
        app.state.search_media = {}
        housekeeping = asyncio.create_task(tracker.housekeeping())
        if resume_tracker and tstate.enabled:
            daemon.start_tracker(app)
        yield
        await daemon.stop_tracker(app)
        housekeeping.cancel()
        try:
            await housekeeping
        except asyncio.CancelledError:
            pass

    app = FastAPI(title = "lydarr", lifespan = lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins = [],
        allow_credentials = False,
        allow_methods = ["GET", "POST"],
        allow_headers = ["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'none';"
        )
        if not request.url.path.startswith("/api/"):
            # Always revalidate, so an updated UI is picked up without a hard refresh.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(AnilistError)
    async def anilist_failed(request: Request, exc: AnilistError) -> JSONResponse:
        _logger.warning("%s failed: AniList: %s", request.url.path, exc)
        return JSONResponse(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            content = {"error": f"AniList: {exc}", "retry_after": getattr(exc, "retry_after", None)},
        )

    @app.exception_handler(NyaaError)
    async def nyaa_failed(request: Request, exc: NyaaError) -> JSONResponse:
        _logger.warning("%s failed: Nyaa: %s", request.url.path, exc)
        return JSONResponse(
            status_code = status.HTTP_502_BAD_GATEWAY,
            content = {"error": f"Nyaa: {exc or type(exc).__name__}"},
        )

    auth = _make_auth_dependency(cfg)

    for module in (anime, torrents, daemon, activity):
        app.include_router(module.router, dependencies = [Depends(auth)])

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory = str(static_dir), html = True), name = "static")

    return app
