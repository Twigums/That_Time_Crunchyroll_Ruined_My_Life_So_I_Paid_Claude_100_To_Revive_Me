import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from lydarr.torrent_client import is_client_up, wait_for_client, add_magnet, _rpc


URL = "http://localhost:9091/transmission/rpc"
USER = None
PASS = None

RPC_SUCCESS = {"result": "success", "arguments": {"torrent-added": {"id": 7}}}
RPC_DUPLICATE = {"result": "duplicate torrent", "arguments": {"torrent-duplicate": {"id": 7}}}
RPC_FAILURE = {"result": "no such torrent", "arguments": {}}
RPC_HEALTH = {"result": "success", "arguments": {"torrents": []}}


def _mock_response(status_code: int, json_data: dict, headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    r.headers = headers or {}
    return r


@pytest.mark.asyncio
async def test_rpc_success_on_first_try():
    ok_response = _mock_response(200, RPC_SUCCESS)

    with patch("lydarr.torrent_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value = mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value = False)
        mock_client.post = AsyncMock(return_value = ok_response)

        result = await _rpc(URL, USER, PASS, "session-get")

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_rpc_409_retry_flow():
    session_id_response = _mock_response(
        409, {}, headers = {"X-Transmission-Session-Id": "abc123"}
    )
    ok_response = _mock_response(200, RPC_SUCCESS)

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return session_id_response
        return ok_response

    with patch("lydarr.torrent_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value = mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value = False)
        mock_client.post = AsyncMock(side_effect = mock_post)

        import lydarr.torrent_client as tc
        tc._session_id = "0"
        result = await _rpc(URL, USER, PASS, "torrent-add")

    assert call_count == 2
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_rpc_409_updates_session_id():
    session_id_response = _mock_response(
        409, {}, headers = {"X-Transmission-Session-Id": "newid456"}
    )
    ok_response = _mock_response(200, RPC_SUCCESS)

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return session_id_response
        return ok_response

    with patch("lydarr.torrent_client.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value = mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value = False)
        mock_client.post = AsyncMock(side_effect = mock_post)

        import lydarr.torrent_client as tc
        tc._session_id = "0"
        await _rpc(URL, USER, PASS, "torrent-add")
        assert tc._session_id == "newid456"


@pytest.mark.asyncio
async def test_is_client_up_true():
    ok_response = _mock_response(200, RPC_HEALTH)

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = ok_response):
        result = await is_client_up(URL, USER, PASS)

    assert result is True


@pytest.mark.asyncio
async def test_is_client_up_false_non_200():
    bad_response = _mock_response(500, {})

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = bad_response):
        result = await is_client_up(URL, USER, PASS)

    assert result is False


@pytest.mark.asyncio
async def test_is_client_up_false_on_exception():
    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, side_effect = Exception("Connection refused")):
        result = await is_client_up(URL, USER, PASS)

    assert result is False


@pytest.mark.asyncio
async def test_wait_for_client_succeeds_immediately():
    with patch("lydarr.torrent_client.is_client_up", new_callable = AsyncMock, return_value = True):
        await wait_for_client(URL, USER, PASS)


@pytest.mark.asyncio
async def test_wait_for_client_retries():
    call_count = 0

    async def mock_is_up(url, user, password):
        nonlocal call_count
        call_count += 1
        return call_count >= 3

    with patch("lydarr.torrent_client.is_client_up", side_effect = mock_is_up), \
         patch("lydarr.torrent_client.asyncio.sleep", new_callable = AsyncMock):
        await wait_for_client(URL, USER, PASS)

    assert call_count == 3


@pytest.mark.asyncio
async def test_add_magnet_success():
    ok_response = _mock_response(200, RPC_SUCCESS)

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = ok_response):
        result = await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH")

    assert result == 7


@pytest.mark.asyncio
async def test_add_magnet_duplicate_is_success():
    dup_response = _mock_response(200, RPC_DUPLICATE)

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = dup_response):
        result = await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH")

    assert result == 7


@pytest.mark.asyncio
async def test_add_magnet_failure():
    fail_response = _mock_response(200, RPC_FAILURE)

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = fail_response):
        result = await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH")

    assert result is None


@pytest.mark.asyncio
async def test_add_magnet_non_200_status():
    bad_response = _mock_response(500, {})

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = bad_response):
        result = await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH")

    assert result is None


@pytest.mark.asyncio
async def test_add_magnet_exception_returns_none():
    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, side_effect = Exception("Error")):
        result = await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH")

    assert result is None


@pytest.mark.asyncio
async def test_add_magnet_with_download_dir():
    ok_response = _mock_response(200, RPC_SUCCESS)
    captured_args = []

    async def mock_rpc(url, user, password, method, arguments = None):
        captured_args.append(arguments)
        return ok_response

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, side_effect = mock_rpc):
        await add_magnet(URL, USER, PASS, "magnet:?xt=urn:btih:HASH", "/downloads")

    assert captured_args[0]["download-dir"] == "/downloads"


@pytest.mark.asyncio
async def test_add_magnet_with_auth():
    ok_response = _mock_response(200, RPC_SUCCESS)

    with patch("lydarr.torrent_client._rpc", new_callable = AsyncMock, return_value = ok_response) as mock_rpc:
        await add_magnet(URL, "admin", "secret", "magnet:?xt=urn:btih:HASH")

    mock_rpc.assert_called_once()
    call_args = mock_rpc.call_args
    assert call_args[0][1] == "admin"
    assert call_args[0][2] == "secret"
