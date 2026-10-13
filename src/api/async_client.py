import asyncio
from collections.abc import Callable
from typing import Any, Self

import httpx

from src.api.base import BaseLibraryClient
from src.config import Settings
from src.exceptions import APIError

ProgressCallback = Callable[[int, int], None]


class AsyncLibraryClient(BaseLibraryClient):
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        super().__init__(settings)
        concurrency = self.settings.max_concurrency
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=concurrency),
        )
        self._semaphore = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                async with self._semaphore:
                    response = await self._client.get(path, params=params)
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
            await asyncio.sleep(delay)
        raise APIError(f"Request failed after retries: {url}", url=url)

    async def search(
        self, query: str, field: str | None = None, page: int = 1, limit: int = 20
    ) -> dict[str, Any]:
        return await self._get(*self.search_request(query, field, page, limit))

    async def get_work(self, work_id: str) -> dict[str, Any]:
        return await self._get(*self.work_request(work_id))

    async def get_author_works(
        self, author_id: str, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return await self._get(*self.author_works_request(author_id, limit, offset))

    async def get_subject(self, subject: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return await self._get(*self.subject_request(subject, limit, offset))

    async def fetch_subject_pages(
        self,
        subject: str,
        pages: int,
        limit: int | None = None,
        on_page: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        limit = limit or self.settings.page_size
        offsets = self.page_offsets(pages, limit)
        completed = 0

        async def fetch(offset: int) -> dict[str, Any]:
            nonlocal completed
            payload = await self.get_subject(subject, limit, offset)
            completed += 1
            if on_page:
                on_page(completed, pages)
            return payload

        return list(await asyncio.gather(*(fetch(offset) for offset in offsets)))
