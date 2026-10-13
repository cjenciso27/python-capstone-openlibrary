from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import BASE_DIR, Settings


def test_defaults_resolve_paths_inside_project():
    settings = Settings(_env_file=None)
    assert settings.api_base_url == "https://openlibrary.org"
    assert settings.db_path.is_absolute()
    assert settings.db_path.parent == BASE_DIR / "data"


def test_relative_paths_are_resolved_against_project_root():
    settings = Settings(_env_file=None, output_dir=Path("custom/reports"))
    assert settings.output_dir == BASE_DIR / "custom" / "reports"


def test_env_file_values_are_loaded(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LIBRARY_PAGE_SIZE=42\nLIBRARY_LOG_LEVEL=debug\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.page_size == 42
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("field", "value"),
    [("log_level", "verbose"), ("max_concurrency", 0), ("api_base_url", "openlibrary.org")],
)
def test_invalid_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_env_key_uses_prefix():
    assert Settings.env_key("page_size") == "LIBRARY_PAGE_SIZE"
