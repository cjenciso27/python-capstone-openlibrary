class LibraryAppError(Exception):
    """Base class for every application-level error."""


class ConfigurationError(LibraryAppError):
    pass


class APIError(LibraryAppError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.retryable = retryable


class NotFoundError(APIError):
    def __init__(self, message: str, url: str | None = None) -> None:
        super().__init__(message, status_code=404, url=url)


class RateLimitError(APIError):
    def __init__(
        self, message: str, url: str | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, status_code=429, url=url, retryable=True)
        self.retry_after = retry_after


class DataValidationError(LibraryAppError):
    pass


class DatabaseError(LibraryAppError):
    pass


class ReportGenerationError(LibraryAppError):
    pass
