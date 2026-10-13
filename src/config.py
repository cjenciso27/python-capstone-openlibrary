from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.exceptions import ConfigurationError

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_PREFIX = "LIBRARY_"
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix=ENV_PREFIX,
        extra="ignore",
    )

    api_base_url: str = "https://openlibrary.org"
    user_agent: str = "OpenLibraryCatalogAutomation/1.0 (student project)"
    request_timeout: float = Field(default=15.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_factor: float = Field(default=0.5, ge=0)
    max_concurrency: int = Field(default=5, ge=1, le=20)
    page_size: int = Field(default=20, ge=1, le=100)
    db_path: Path = BASE_DIR / "data" / "library.db"
    cache_ttl_hours: float = Field(default=24, gt=0)
    output_dir: Path = BASE_DIR / "data" / "outputs"
    log_dir: Path = BASE_DIR / "logs"
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(LOG_LEVELS)}")
        return level

    @field_validator("api_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("api_base_url must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("db_path", "output_dir", "log_dir")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else BASE_DIR / value

    @staticmethod
    def env_key(field_name: str) -> str:
        return f"{ENV_PREFIX}{field_name.upper()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def editable_fields() -> list[str]:
    return list(Settings.model_fields)


def read_env_file(env_file: Path = ENV_FILE) -> dict[str, str]:
    """Parses KEY=VALUE pairs from a .env file, ignoring comments and blank lines."""
    if not env_file.is_file():
        return {}
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def update_env_value(field_name: str, value: str, env_file: Path = ENV_FILE) -> Settings:
    """Validates a new setting value and persists it to the .env file.

    Existing lines and comments are preserved; the key is appended when missing.
    Raises ConfigurationError when the field is unknown or the value is invalid.
    """
    if field_name not in Settings.model_fields:
        raise ConfigurationError(f"Unknown setting: {field_name}")
    try:
        Settings(_env_file=env_file if env_file.is_file() else None, **{field_name: value})
    except ValidationError as exc:
        reason = exc.errors()[0].get("msg", str(exc))
        raise ConfigurationError(f"Invalid value for {field_name}: {reason}") from exc

    key = Settings.env_key(field_name)
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    new_line = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key:
            lines[index] = new_line
            break
    else:
        lines.append(new_line)
    try:
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"Cannot write {env_file}: {exc}") from exc
    return Settings(_env_file=env_file)
