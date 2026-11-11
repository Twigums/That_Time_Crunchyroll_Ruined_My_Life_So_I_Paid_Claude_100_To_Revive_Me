import base64
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from lydarr.file_manager import MediaEntry
from lydarr.state import TrackerState
from lydarr.tests.webrig import WebRig, make_cfg


def _quiet_tracker(rig: WebRig):
    """Let the tracker loop run without doing anything."""
    return patch.object(rig.tracker, "tick", new_callable = AsyncMock)


def test_status_reports_tracker_and_transmission_in_one_call(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = True):
        with TestClient(rig.app) as client:
            data = client.get("/api/status").json()
    assert data == {
        "tracker": {"running": False, "started_at": None},
        "transmission": {"online": True},
        "review": 0,
    }


def test_status_counts_items_waiting_for_review(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "Show")])
    rig.tstate.episode("Show", 4).status = "review"
    with patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = False):
        with TestClient(rig.app) as client:
            data = client.get("/api/status").json()
    assert data["review"] == 1
    assert data["transmission"] == {"online": False}


def test_old_status_routes_still_answer(tmp_path):
    rig = WebRig(tmp_path, [MediaEntry(title = "A"), MediaEntry(title = "B", deprecated = True)])
    with patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = True):
        with TestClient(rig.app) as client:
            daemon = client.get("/api/daemon/status").json()
            legacy = client.get("/api/rtorrent/status").json()
            current = client.get("/api/transmission/status").json()
    assert daemon == {"running": False, "started_at": None, "tracking": ["A"]}
    assert legacy == current == {"online": True}


def test_start_and_stop_toggle_the_tracker_and_remember_the_choice(tmp_path):
    rig = WebRig(tmp_path)
    with _quiet_tracker(rig), TestClient(rig.app) as client:
        assert client.post("/api/daemon/start").json() == {"ok": True}
        running = client.get("/api/daemon/status").json()
        assert running["running"] is True and running["started_at"] is not None
        assert client.post("/api/daemon/start").json() == {"ok": False, "reason": "already running"}

        saved = TrackerState(rig.cfg.state_file)
        saved.load()
        assert saved.enabled is True

        assert client.post("/api/daemon/stop").json() == {"ok": True}
        assert client.get("/api/daemon/status").json()["running"] is False
        assert client.post("/api/daemon/stop").json() == {"ok": False, "reason": "not running"}

    saved = TrackerState(rig.cfg.state_file)
    saved.load()
    assert saved.enabled is False


def test_tracker_starts_with_an_empty_watchlist(tmp_path):
    rig = WebRig(tmp_path)
    with _quiet_tracker(rig), TestClient(rig.app) as client:
        assert client.post("/api/daemon/start").json() == {"ok": True}


def test_tracker_resumes_on_boot_when_it_was_left_running(tmp_path):
    cfg = make_cfg(tmp_path)
    earlier = TrackerState(cfg.state_file)
    earlier.enabled = True
    import asyncio
    asyncio.run(earlier.save())

    rig = WebRig(tmp_path, cfg = cfg, resume_tracker = True)
    rig.tstate.load()
    with _quiet_tracker(rig), TestClient(rig.app) as client:
        assert client.get("/api/daemon/status").json()["running"] is True


def test_tracker_stays_off_on_boot_when_it_was_stopped(tmp_path):
    rig = WebRig(tmp_path, resume_tracker = True)
    with _quiet_tracker(rig), TestClient(rig.app) as client:
        assert client.get("/api/daemon/status").json()["running"] is False


# ── the single Start / Stop control ──────────────────────────────────────────

def test_power_start_brings_up_transmission_then_the_tracker(tmp_path):
    rig = WebRig(tmp_path)
    up = AsyncMock(side_effect = [False, True])
    with _quiet_tracker(rig), \
         patch("lydarr.web.routes.daemon.is_client_up", up), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock, return_value = (0, "")) as systemctl, \
         patch("lydarr.web.routes.daemon._pause", new_callable = AsyncMock):
        with TestClient(rig.app) as client:
            data = client.post("/api/power/start").json()

    systemctl.assert_awaited_once()
    assert systemctl.await_args.args[1] == "start"
    assert data["ok"] is True
    assert data["tracker"]["running"] is True
    assert data["transmission"] == {"ok": True, "started": True}


def test_power_start_with_transmission_already_up_only_starts_the_tracker(tmp_path):
    rig = WebRig(tmp_path)
    with _quiet_tracker(rig), \
         patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = True), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock) as systemctl:
        with TestClient(rig.app) as client:
            data = client.post("/api/power/start").json()

    systemctl.assert_not_awaited()
    assert data["tracker"]["running"] is True
    assert data["transmission"] == {"ok": True, "started": False}


def test_power_start_still_starts_the_tracker_when_transmission_will_not(tmp_path):
    rig = WebRig(tmp_path)
    with _quiet_tracker(rig), \
         patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = False), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock,
               return_value = (5, "Unit transmission-daemon.service not found.")):
        with TestClient(rig.app) as client:
            data = client.post("/api/power/start").json()

    assert data["ok"] is False
    assert data["reason"] == "Unit transmission-daemon.service not found."
    assert data["tracker"]["running"] is True


def test_power_stop_stops_the_tracker_then_transmission(tmp_path):
    rig = WebRig(tmp_path)
    up = AsyncMock(side_effect = [True, True, False])
    with _quiet_tracker(rig), \
         patch("lydarr.web.routes.daemon.is_client_up", up), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock, return_value = (0, "")) as systemctl, \
         patch("lydarr.web.routes.daemon._pause", new_callable = AsyncMock):
        with TestClient(rig.app) as client:
            client.post("/api/power/start")
            data = client.post("/api/power/stop").json()

    assert systemctl.await_args.args[1] == "stop"
    assert data["ok"] is True
    assert data["tracker"]["running"] is False
    assert data["transmission"] == {"ok": True, "stopped": True}


def test_transmission_start_times_out_cleanly(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = False), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock, return_value = (0, "")), \
         patch("lydarr.web.routes.daemon._pause", new_callable = AsyncMock):
        with TestClient(rig.app) as client:
            data = client.post("/api/transmission/start").json()
    assert data == {"ok": False, "reason": "Transmission did not come up in time"}


def test_transmission_stop_when_already_down_is_a_no_op(tmp_path):
    rig = WebRig(tmp_path)
    with patch("lydarr.web.routes.daemon.is_client_up", new_callable = AsyncMock, return_value = False), \
         patch("lydarr.web.routes.daemon._systemctl", new_callable = AsyncMock) as systemctl:
        with TestClient(rig.app) as client:
            data = client.post("/api/transmission/stop").json()
    assert data == {"ok": True, "stopped": False}
    systemctl.assert_not_awaited()


def test_systemctl_uses_the_configured_unit_and_scope(tmp_path):
    import asyncio
    from lydarr.web.routes.daemon import _systemctl

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    for scope, expected in (
        ("user", ["systemctl", "--user", "start", "my-transmission"]),
        ("system", ["sudo", "-n", "systemctl", "start", "my-transmission"]),
    ):
        cfg = make_cfg(tmp_path, transmission_unit = "my-transmission", transmission_scope = scope)
        with patch("lydarr.web.routes.daemon.asyncio.create_subprocess_exec", new_callable = AsyncMock,
                   return_value = _Proc()) as spawn:
            assert asyncio.run(_systemctl(cfg, "start")) == (0, "")
        assert list(spawn.await_args.args) == expected


# ── auth and headers ─────────────────────────────────────────────────────────

def test_api_requires_credentials_when_configured(tmp_path):
    rig = WebRig(tmp_path, cfg = make_cfg(tmp_path, lydarr_user = "admin", lydarr_pass = "secret"))
    good = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()}
    with TestClient(rig.app) as client:
        assert client.get("/api/anime/list").status_code == 401
        assert client.get("/api/anime/list", headers = bad).status_code == 401
        assert client.get("/api/anime/list", headers = good).status_code == 200


def test_pages_forbid_inline_scripts_and_always_revalidate(tmp_path):
    rig = WebRig(tmp_path)
    with TestClient(rig.app) as client:
        page = client.get("/")
        api = client.get("/api/anime/list")
    assert page.status_code == 200
    assert "script-src 'self';" in page.headers["Content-Security-Policy"]
    assert page.headers["Cache-Control"] == "no-cache"
    assert "Cache-Control" not in api.headers
