import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from anilist import http
from anilist.types import AnilistError, AnilistRateLimited, AnilistUnavailable


@pytest.fixture(autouse = True)
def fresh_gate():
    http.reset_gate()
    with patch("anilist.http.asyncio.sleep", new_callable = AsyncMock):
        yield
    http.reset_gate()


def _response(status: int, body: dict | None = None, headers: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.text = text
    if body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = body
    return r


def _client(*responses):
    post = AsyncMock(side_effect = list(responses))
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value = MagicMock(post = post))
    cls.return_value.__aexit__ = AsyncMock(return_value = False)
    return cls, post


@pytest.mark.asyncio
async def test_returns_data_on_success():
    cls, post = _client(_response(200, {"data": {"ok": 1}}))
    with patch("anilist.http.httpx.AsyncClient", cls):
        assert await http.graphql("q", {}) == {"ok": 1}
    assert post.call_count == 1


@pytest.mark.asyncio
async def test_graphql_errors_raise_without_tripping_breaker():
    cls, _ = _client(_response(200, {"errors": [{"message": "bad field"}]}), _response(200, {"data": {}}))
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistError, match = "bad field"):
            await http.graphql("q", {})
        assert await http.graphql("q", {}) == {}


@pytest.mark.asyncio
async def test_rate_limit_pauses_every_caller():
    cls, post = _client(_response(429, {"errors": [{"message": "Too Many Requests."}]}, {"Retry-After": "40"}))
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistRateLimited) as first:
            await http.graphql("q", {})
        with pytest.raises(AnilistRateLimited):
            await http.graphql("q", {}, interactive = True)
    assert first.value.retry_after == 40
    assert post.call_count == 1
    assert http.gate_status()["rate_limited"] is True


@pytest.mark.asyncio
async def test_short_rate_limit_window_is_waited_out():
    cls, post = _client(
        _response(429, {}, {"Retry-After": "2"}),
        _response(200, {"data": {"ok": 1}}),
    )
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistRateLimited):
            await http.graphql("q", {})
        assert await http.graphql("q", {}) == {"ok": 1}
    assert post.call_count == 2


@pytest.mark.asyncio
async def test_outage_opens_breaker_for_background_calls():
    body = {"errors": [{"message": "The AniList API has been temporarily disabled.", "status": 403}]}
    cls, post = _client(_response(403, body), _response(200, {"data": {"ok": 1}}))
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistUnavailable, match = "temporarily disabled"):
            await http.graphql("q", {})
        with pytest.raises(AnilistUnavailable):
            await http.graphql("q", {})
        assert post.call_count == 1
        assert await http.graphql("q", {}, interactive = True) == {"ok": 1}
    assert http.gate_status()["blocked_for"] == 0


@pytest.mark.asyncio
async def test_network_errors_become_anilist_errors():
    cls, _ = _client(httpx.ConnectTimeout(""))
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistUnavailable, match = "ConnectTimeout"):
            await http.graphql("q", {})


@pytest.mark.asyncio
async def test_error_message_is_one_line_for_html_bodies():
    html = "<html>\n<head><title>502 Bad Gateway</title></head>\n</html>"
    cls, _ = _client(_response(502, None, text = html))
    with patch("anilist.http.httpx.AsyncClient", cls):
        with pytest.raises(AnilistUnavailable) as err:
            await http.graphql("q", {})
    assert "\n" not in str(err.value)


@pytest.mark.asyncio
async def test_request_spacing_follows_advertised_limit():
    cls, _ = _client(_response(200, {"data": {}}, {"X-RateLimit-Limit": "30"}))
    with patch("anilist.http.httpx.AsyncClient", cls):
        await http.graphql("q", {})
    assert http._gate.interval == pytest.approx(2.1)
