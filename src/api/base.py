import random
from typing import Any

import httpx

from src.config import Settings, get_settings
from src.exceptions import APIError, DataValidationError, NotFoundError, RateLimitError
from src.logger import get_logger

SEARCH_FIELDS = (
    "key,title,author_name,author_key,first_publish_year,edition_count,language,isbn,subject"
)
SEARCH_TYPES = ("q", "author", "title", "isbn")
MAX_RETRY_AFTER = 60.0

Request = tuple[str, dict[str, Any]]


def strip_key(value: str | None, prefix: str) -> str:
    if not value:
        return ""
    value = value.strip()
    return value[len(prefix) :] if value.startswith(prefix) else value.rsplit("/", 1)[-1]


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


class BaseLibraryClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.api_base_url.rstrip("/")
        self.max_retries = self.settings.max_retries
        self.headers = {"User-Agent": self.settings.user_agent, "Accept": "application/json"}
        self.timeout = httpx.Timeout(self.settings.request_timeout)
        self.logger = get_logger(f"api.{type(self).__name__}")

    @staticmethod
    def search_request(
        query: str, field: str | None = None, page: int = 1, limit: int = 20
    ) -> Request:
        field = field or "q"
        if field not in SEARCH_TYPES:
            raise DataValidationError(f"Unsupported search field: {field}")
        params: dict[str, Any] = {field: query, "page": page, "limit": limit}
        params["fields"] = SEARCH_FIELDS
        return "/search.json", params

    @staticmethod
    def work_request(work_id: str) -> Request:
        return f"/works/{strip_key(work_id, '/works/')}.json", {}

    @staticmethod
    def author_works_request(author_id: str, limit: int = 50, offset: int = 0) -> Request:
        author = strip_key(author_id, "/authors/")
        return f"/authors/{author}/works.json", {"limit": limit, "offset": offset}

    @staticmethod
    def subject_request(subject: str, limit: int = 20, offset: int = 0) -> Request:
        return f"/subjects/{subject}.json", {"limit": limit, "offset": offset}

    @staticmethod
    def page_offsets(pages: int, limit: int) -> list[int]:
        if pages < 1 or limit < 1:
            raise DataValidationError("Pages and limit must be positive integers.")
        return [page * limit for page in range(pages)]

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        if isinstance(error, httpx.TransportError):
            return True
        return isinstance(error, APIError) and error.retryable

    def retry_delay(self, attempt: int, error: Exception) -> float:
        if isinstance(error, RateLimitError) and error.retry_after is not None:
            return min(error.retry_after, MAX_RETRY_AFTER)
        return self.settings.backoff_factor * (2**attempt) + random.uniform(0, 0.1)

    def network_error(self, error: httpx.TransportError, url: str) -> APIError:
        return APIError(
            f"Network error contacting Open Library ({type(error).__name__}): {error}",
            url=url,
            retryable=True,
        )

    def handle_response(self, response: httpx.Response, url: str) -> dict[str, Any]:
        status = response.status_code
        if status == 429:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            raise RateLimitError("Open Library rate limit reached.", url, retry_after)
        if status == 404:
            raise NotFoundError(f"Resource not found: {url}", url=url)
        if status >= 500:
            raise APIError(f"Open Library server error ({status}).", status, url, retryable=True)
        if status >= 400:
            raise APIError(f"Request rejected by Open Library ({status}).", status, url)

        try:
            data = response.json()
        except ValueError as exc:
            raise DataValidationError(f"Invalid JSON received from {url}") from exc
        if not isinstance(data, dict):
            raise DataValidationError(f"Unexpected payload type from {url}: {type(data).__name__}")
        return data
