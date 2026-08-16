"""FastAPI application factory."""
import asyncio
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from lydarr.config import AppConfig
from lydarr.file_manager import MediaState

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


def create_app(cfg: AppConfig, state: MediaState) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cfg = cfg
        app.state.anime_state = state
        app.state.daemon_task = None
        app.state.daemon_started_at = None
        app.state.tracker_tasks = set()
        app.state.status_cache = {}
        app.state.status_inflight = {}
        app.state.schedule_cache = {}
        app.state.schedule_inflight = {}
        app.state.status_lock = asyncio.Lock()
        yield

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
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'none';"
        )
        return response

    auth = _make_auth_dependency(cfg)

    from lydarr.web.routes import anime, torrents, daemon
    app.include_router(anime.router, dependencies = [Depends(auth)])
    app.include_router(torrents.router, dependencies = [Depends(auth)])
    app.include_router(daemon.router, dependencies = [Depends(auth)])

    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory = str(static_dir), html = True), name = "static")

    return app
