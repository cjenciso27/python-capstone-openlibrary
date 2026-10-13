from pathlib import Path

import pytest

from src.config import Settings
from src.db.manager import DatabaseManager
from src.models import AuthorRecord, WorkRecord


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_path / "test.db",
        output_dir=tmp_path / "outputs",
        log_dir=tmp_path / "logs",
        max_retries=2,
        backoff_factor=0,
        page_size=2,
        max_concurrency=3,
    )


@pytest.fixture
def db(settings: Settings) -> DatabaseManager:
    return DatabaseManager(settings.db_path, settings.cache_ttl_hours)


@pytest.fixture
def records() -> list[WorkRecord]:
    tolkien = AuthorRecord("OL26320A", "J.R.R. Tolkien")
    marquez = AuthorRecord("OL4586796A", "Gabriel García Márquez")
    return [
        WorkRecord(
            work_key="OL262758W",
            title="The Hobbit",
            authors=[tolkien],
            first_publish_year=1937,
            edition_count=52,
            languages=["eng", "spa"],
            isbn="9780547928227",
            subjects=["Fantasy", "Adventure"],
            source="search",
        ),
        WorkRecord(
            work_key="OL27482W",
            title="The Silmarillion",
            authors=[tolkien],
            first_publish_year=1977,
            edition_count=18,
            languages=["eng"],
            subjects=["Fantasy"],
            source="search",
        ),
        WorkRecord(
            work_key="OL274505W",
            title="Cien años de soledad",
            authors=[marquez],
            first_publish_year=1967,
            edition_count=40,
            languages=["spa"],
            isbn="9780307474728",
            subjects=["Novela"],
            source="catalog",
        ),
    ]


@pytest.fixture
def seeded_db(db: DatabaseManager, records: list[WorkRecord]) -> DatabaseManager:
    db.upsert_works(records)
    return db


@pytest.fixture
def subject_payload() -> dict:
    return {
        "name": "fantasy",
        "work_count": 2,
        "works": [
            {
                "key": "/works/OL262758W",
                "title": "The Hobbit",
                "edition_count": 52,
                "first_publish_year": 1937,
                "authors": [{"key": "/authors/OL26320A", "name": "J.R.R. Tolkien"}],
                "subject": ["Fantasy", "Dragons"],
                "availability": {"isbn": "9780547928227"},
            },
            {"key": "/works/OL1W", "title": ""},
        ],
    }


@pytest.fixture
def search_payload() -> dict:
    return {
        "numFound": 1,
        "docs": [
            {
                "key": "/works/OL45804W",
                "title": "Fantastic Mr Fox",
                "author_name": ["Roald Dahl"],
                "author_key": ["OL34184A"],
                "first_publish_year": 1970,
                "edition_count": 25,
                "language": ["eng", "spa"],
                "isbn": ["0140328726", "9780140328721", "invalid"],
                "subject": ["Animals", "Foxes"],
            },
            {"key": "", "title": "Broken doc"},
        ],
    }
