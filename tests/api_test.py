from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api.async_client import AsyncLibraryClient
from src.api.base import BaseLibraryClient, parse_retry_after, strip_key
from src.api.sync_client import SyncLibraryClient
from src.exceptions import APIError, DataValidationError, NotFoundError, RateLimitError

BASE = "https://openlibrary.org"


def make_response(status: int = 200, json_data=None, text: str | None = None, headers=None):
    request = httpx.Request("GET", f"{BASE}/test")
    if text is not None:
        return httpx.Response(status, text=text, headers=headers, request=request)
    return httpx.Response(status, json=json_data or {}, headers=headers, request=request)


@pytest.fixture
def mock_http():
    return MagicMock(spec=httpx.Client)


@pytest.fixture
def sync_client(settings, mock_http):
    return SyncLibraryClient(settings, client=mock_http)


@pytest.fixture
def mock_async_http():
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock()
    http.aclose = AsyncMock()
    return http


@pytest.fixture
def async_client(settings, mock_async_http):
    return AsyncLibraryClient(settings, client=mock_async_http)


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    ("value", "prefix", "expected"),
    [
        ("/works/OL45804W", "/works/", "OL45804W"),
        ("OL45804W", "/works/", "OL45804W"),
        ("https://openlibrary.org/authors/OL1A", "/authors/", "OL1A"),
        (None, "/works/", ""),
    ],
)
def test_strip_key(value, prefix, expected):
    assert strip_key(value, prefix) == expected


@pytest.mark.parametrize(("raw", "expected"), [("5", 5.0), ("-2", 0.0), ("soon", None), (None, None)])
def test_parse_retry_after(raw, expected):
    assert parse_retry_after(raw) == expected


def test_search_request_builds_params():
    path, params = BaseLibraryClient.search_request("tolkien", "author", page=2, limit=5)
    assert path == "/search.json"
    assert params["author"] == "tolkien"
    assert params["page"] == 2
    assert params["limit"] == 5
    assert "fields" in params


def test_search_request_defaults_to_q_and_rejects_unknown_field():
    _, params = BaseLibraryClient.search_request("dune")
    assert params["q"] == "dune"
    with pytest.raises(DataValidationError):
        BaseLibraryClient.search_request("dune", "publisher")


def test_endpoint_builders():
    assert BaseLibraryClient.work_request("/works/OL1W") == ("/works/OL1W.json", {})
    path, params = BaseLibraryClient.author_works_request("OL26320A", limit=10)
    assert path == "/authors/OL26320A/works.json"
    assert params == {"limit": 10, "offset": 0}
    path, params = BaseLibraryClient.subject_request("fantasy", limit=20, offset=40)
    assert path == "/subjects/fantasy.json"
    assert params == {"limit": 20, "offset": 40}


def test_page_offsets():
    assert BaseLibraryClient.page_offsets(3, 20) == [0, 20, 40]
    with pytest.raises(DataValidationError):
        BaseLibraryClient.page_offsets(0, 20)


def test_retry_delay_honours_retry_after(settings):
    client = SyncLibraryClient(settings, client=MagicMock(spec=httpx.Client))
    error = RateLimitError("slow down", retry_after=7)
    assert client.retry_delay(0, error) == 7
    assert client.retry_delay(0, RateLimitError("slow down", retry_after=999)) == 60.0


# -------------------------------------------------------------- sync client
def test_sync_search_returns_payload(sync_client, mock_http, search_payload):
    mock_http.get.return_value = make_response(200, search_payload)
    result = sync_client.search("fox", "title")
    assert result["numFound"] == 1
    mock_http.get.assert_called_once()
    path = mock_http.get.call_args.args[0]
    assert path == "/search.json"
    assert mock_http.get.call_args.kwargs["params"]["title"] == "fox"


def test_sync_not_found_is_not_retried(sync_client, mock_http):
    mock_http.get.return_value = make_response(404)
    with pytest.raises(NotFoundError):
        sync_client.get_work("OL0W")
    assert mock_http.get.call_count == 1


def test_sync_client_error_is_not_retried(sync_client, mock_http):
    mock_http.get.return_value = make_response(400)
    with pytest.raises(APIError) as info:
        sync_client.get_work("OL0W")
    assert info.value.status_code == 400
    assert mock_http.get.call_count == 1


@patch("src.api.sync_client.time.sleep")
def test_sync_retries_server_errors_then_succeeds(mock_sleep, sync_client, mock_http):
    mock_http.get.side_effect = [make_response(503), make_response(200, {"ok": True})]
    assert sync_client.get_subject("fantasy") == {"ok": True}
    assert mock_http.get.call_count == 2
    mock_sleep.assert_called_once()


@patch("src.api.sync_client.time.sleep")
def test_sync_rate_limit_uses_retry_after(mock_sleep, sync_client, mock_http):
    mock_http.get.side_effect = [
        make_response(429, headers={"Retry-After": "3"}),
        make_response(200, {"ok": True}),
    ]
    assert sync_client.get_work("OL1W") == {"ok": True}
    mock_sleep.assert_called_once_with(3.0)


@patch("src.api.sync_client.time.sleep")
def test_sync_gives_up_after_max_retries(mock_sleep, sync_client, mock_http):
    mock_http.get.return_value = make_response(500)
    with pytest.raises(APIError) as info:
        sync_client.get_work("OL1W")
    assert info.value.retryable
    assert mock_http.get.call_count == 3  # 1 attempt + 2 retries
    assert mock_sleep.call_count == 2


@patch("src.api.sync_client.time.sleep")
def test_sync_transport_errors_become_api_error(mock_sleep, sync_client, mock_http):
    mock_http.get.side_effect = httpx.ConnectError("offline")
    with pytest.raises(APIError, match="Network error"):
        sync_client.search("dune")
    assert mock_http.get.call_count == 3


def test_sync_invalid_json_raises_validation_error(sync_client, mock_http):
    mock_http.get.return_value = make_response(200, text="<html>oops</html>")
    with pytest.raises(DataValidationError):
        sync_client.search("dune")


def test_sync_non_object_payload_is_rejected(sync_client, mock_http):
    mock_http.get.return_value = make_response(200, text="[1, 2, 3]")
    with pytest.raises(DataValidationError):
        sync_client.search("dune")


def test_sync_fetch_subject_pages_reports_progress(sync_client, mock_http):
    mock_http.get.side_effect = [make_response(200, {"page": i}) for i in range(3)]
    progress = MagicMock()
    pages = sync_client.fetch_subject_pages("fantasy", pages=3, limit=10, on_page=progress)
    assert [p["page"] for p in pages] == [0, 1, 2]
    offsets = [c.kwargs["params"]["offset"] for c in mock_http.get.call_args_list]
    assert offsets == [0, 10, 20]
    assert progress.call_count == 3
    progress.assert_called_with(3, 3)


def test_sync_context_manager_closes_client(settings, mock_http):
    with SyncLibraryClient(settings, client=mock_http):
        pass
    mock_http.close.assert_called_once()


# ------------------------------------------------------------- async client
async def test_async_search_returns_payload(async_client, mock_async_http, search_payload):
    mock_async_http.get.return_value = make_response(200, search_payload)
    result = await async_client.search("fox")
    assert result["docs"][0]["title"] == "Fantastic Mr Fox"


@patch("src.api.async_client.asyncio.sleep", new_callable=AsyncMock)
async def test_async_retries_then_succeeds(mock_sleep, async_client, mock_async_http):
    mock_async_http.get.side_effect = [
        httpx.ReadTimeout("slow"),
        make_response(200, {"ok": True}),
    ]
    assert await async_client.get_work("OL1W") == {"ok": True}
    assert mock_async_http.get.await_count == 2
    mock_sleep.assert_awaited_once()


@patch("src.api.async_client.asyncio.sleep", new_callable=AsyncMock)
async def test_async_gives_up_after_retries(mock_sleep, async_client, mock_async_http):
    mock_async_http.get.return_value = make_response(502)
    with pytest.raises(APIError):
        await async_client.get_subject("fantasy")
    assert mock_async_http.get.await_count == 3


async def test_async_not_found(async_client, mock_async_http):
    mock_async_http.get.return_value = make_response(404)
    with pytest.raises(NotFoundError):
        await async_client.get_author_works("OL0A")


async def test_async_fetch_subject_pages_keeps_order(async_client, mock_async_http):
    async def fake_get(path, params=None):
        return make_response(200, {"offset": params["offset"]})

    mock_async_http.get.side_effect = fake_get
    progress = MagicMock()
    pages = await async_client.fetch_subject_pages("fantasy", pages=4, limit=5, on_page=progress)
    assert [p["offset"] for p in pages] == [0, 5, 10, 15]
    assert progress.call_count == 4


async def test_async_context_manager_closes_client(settings, mock_async_http):
    async with AsyncLibraryClient(settings, client=mock_async_http):
        pass
    mock_async_http.aclose.assert_awaited_once()
