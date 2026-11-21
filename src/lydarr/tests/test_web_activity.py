import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from lydarr.tests.fake_jellyfin import KEY, SERVER_ID, URL, FakeJellyfin
from lydarr.tests.webrig import WebRig, make_cfg

NAME = "[SubsPlease] Show - 05 (1080p) [ABCD1234].mkv"


def _rig(tmp_path, **cfg) -> tuple[WebRig, str]:
    rig = WebRig(tmp_path, cfg = make_cfg(tmp_path, **cfg))
    folder = os.path.join(rig.cfg.default_dir, "Show")
    rig.tstate.log("grabbed", "Show", 5, name = NAME, info_hash = "a" * 40, directory = folder)
    rig.tstate.log("missed", "Show", 6, detail = "nothing found")
    return rig, folder


def test_activity_says_whether_jellyfin_links_can_be_offered(tmp_path):
    off, _ = _rig(tmp_path / "off")
    on, _ = _rig(tmp_path / "on", jellyfin_url = URL, jellyfin_api_key = KEY)
    with TestClient(off.app) as client:
        assert client.get("/api/activity").json()["jellyfin"] is False
    with TestClient(on.app) as client:
        assert client.get("/api/activity").json()["jellyfin"] is True


def test_an_added_episode_redirects_to_its_jellyfin_page(tmp_path):
    rig, folder = _rig(tmp_path, jellyfin_url = URL, jellyfin_api_key = KEY)
    jf = FakeJellyfin({"Anime": [rig.cfg.default_dir]})
    show = jf.add_show("Anime", "Show", folder)
    episode = jf.add_episode(show, os.path.join(folder, NAME), 5)
    with patch("lydarr.jellyfin._request", jf), TestClient(rig.app) as client:
        r = client.get("/api/activity/1/jellyfin", follow_redirects = False)
    assert r.status_code == 307
    assert r.headers["location"] == f"{URL}/web/#/details?id={episode}&serverId={SERVER_ID}"


def test_jellyfin_links_explain_why_they_cannot_be_followed(tmp_path):
    rig, _ = _rig(tmp_path, jellyfin_url = URL, jellyfin_api_key = KEY)
    jf = FakeJellyfin({"Anime": ["/somewhere/else"]})
    with patch("lydarr.jellyfin._request", jf), TestClient(rig.app) as client:
        assert client.get("/api/activity/2/jellyfin").status_code == 404
        assert client.get("/api/activity/99/jellyfin").status_code == 404
        r = client.get("/api/activity/1/jellyfin")
        assert r.status_code == 404 and "JELLYFIN_PATH_MAP" in r.text
        jf.down = True
        r = client.get("/api/activity/1/jellyfin")
        assert r.status_code == 502 and "cannot reach" in r.text


def test_jellyfin_links_need_jellyfin_set_up(tmp_path):
    rig, _ = _rig(tmp_path)
    with TestClient(rig.app) as client:
        r = client.get("/api/activity/1/jellyfin")
    assert r.status_code == 404 and "JELLYFIN_URL" in r.text
