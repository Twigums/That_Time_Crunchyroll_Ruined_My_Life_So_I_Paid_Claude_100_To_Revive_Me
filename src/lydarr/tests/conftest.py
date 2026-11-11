"""Keep the test suite off the network.

Anything that would reach AniList, Nyaa or Transmission fails fast unless a test
supplies its own stand-in, so a test run can never touch a live Transmission
instance on this machine or spend the AniList rate limit.
"""
from unittest.mock import AsyncMock, patch

import pytest

from anilist.types import AnilistUnavailable
from nyaa.types import HttpError


@pytest.fixture(autouse = True)
def no_network():
    with patch("anilist.search.graphql", new_callable = AsyncMock,
               side_effect = AnilistUnavailable("network disabled in tests")), \
         patch("nyaa.http._get", new_callable = AsyncMock,
               side_effect = HttpError("network disabled in tests")), \
         patch("lydarr.torrent_client._rpc", new_callable = AsyncMock,
               side_effect = ConnectionError("network disabled in tests")):
        yield
