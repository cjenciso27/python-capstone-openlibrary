import time
from collections.abc import Callable
from typing import Any, Self

import httpx

from src.api.base import BaseLibraryClient
from src.config import Settings
from src.exceptions import APIError

ProgressCallback = Callable[[int, int], None]


class SyncLibraryClient(BaseLibraryClient):
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        super().__init__(settings)
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(path, params=params)
                return self.handle_response(response, url)
            except APIError as exc:
                if not exc.retryable or attempt == self.max_retries:
                    raise
                error: Exception = exc
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise self.network_error(exc, url) from exc
                error = exc

            delay = self.retry_delay(attempt, error)
            self.logger.warning(
                "Retry %d/%d for %s in %.2fs (%s)",
                attempt + 1,
                self.max_retries,
                path,
                delay,
                error,
            )
            time.sleep(delay)
        raise APIError(f"Request failed after retries: {url}", url=url)

    def search(
        self, query: str, field: str | None = None, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        return self._get(*self.search_request(query, field, page, limit))

    def get_work(self, work_id: str) -> dict[str, Any]:
        return self._get(*self.work_request(work_id))

    def get_author_works(self, author_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._get(*self.author_works_request(author_id, limit, offset))

    def get_subject(self, subject: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return self._get(*self.subject_request(subject, limit, offset))

    def fetch_subject_pages(
        self,
        subject: str,
        pages: int,
        limit: int | None = None,
        on_page: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or self.settings.page_size
        results = []
        for index, offset in enumerate(self.page_offsets(pages, limit), start=1):
            results.append(self.get_subject(subject, limit, offset))
            if on_page:
                on_page(index, pages)
        return results
