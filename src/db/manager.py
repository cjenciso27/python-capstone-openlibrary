import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.exceptions import DatabaseError
from src.logger import get_logger
from src.models import WorkRecord
from src.processing.cleaner import normalize_for_comparison

TABLES = ("works", "authors", "work_authors", "api_cache")
SEARCH_FIELDS = ("author", "title", "isbn")

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    work_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    first_publish_year INTEGER,
    edition_count INTEGER NOT NULL DEFAULT 0,
    languages TEXT NOT NULL DEFAULT '',
    isbn TEXT,
    subjects TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS authors (
    author_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_authors (
    work_key TEXT NOT NULL REFERENCES works(work_key) ON DELETE CASCADE,
    author_key TEXT NOT NULL REFERENCES authors(author_key) ON DELETE CASCADE,
    PRIMARY KEY (work_key, author_key)
);
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_works_isbn ON works(isbn);
CREATE INDEX IF NOT EXISTS idx_works_year ON works(first_publish_year);
CREATE INDEX IF NOT EXISTS idx_authors_name ON authors(name);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON api_cache(expires_at);
INSERT OR IGNORE INTO cache_stats (id, hits, misses) VALUES (1, 0, 0);
"""

UPSERT_AUTHOR = """
INSERT INTO authors (author_key, name, updated_at) VALUES (?, ?, ?)
ON CONFLICT(author_key) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
"""

UPSERT_WORK = """
INSERT INTO works (work_key, title, first_publish_year, edition_count, languages, isbn,
                   subjects, source, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(work_key) DO UPDATE SET
    title = excluded.title,
    first_publish_year = COALESCE(excluded.first_publish_year, works.first_publish_year),
    edition_count = MAX(excluded.edition_count, works.edition_count),
    languages = COALESCE(NULLIF(excluded.languages, ''), works.languages),
    isbn = COALESCE(excluded.isbn, works.isbn),
    subjects = COALESCE(NULLIF(excluded.subjects, ''), works.subjects),
    source = excluded.source,
    updated_at = excluded.updated_at
"""

SEARCH_SELECT = """
SELECT w.work_key, w.title, w.first_publish_year, w.edition_count, w.languages, w.isbn,
       COALESCE(GROUP_CONCAT(a.name, ', '), '') AS authors
FROM works w
LEFT JOIN work_authors wa ON wa.work_key = w.work_key
LEFT JOIN authors a ON a.author_key = wa.author_key
WHERE {condition}
GROUP BY w.work_key
ORDER BY w.edition_count DESC, w.title
LIMIT ?
"""

SEARCH_CONDITIONS = {
    "title": "normalize_text(w.title) LIKE ?",
    "isbn": "w.isbn = ?",
    "author": (
        "w.work_key IN (SELECT wa2.work_key FROM work_authors wa2 "
        "JOIN authors a2 ON a2.author_key = wa2.author_key "
        "WHERE normalize_text(a2.name) LIKE ?)"
    ),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DatabaseManager:
    def __init__(self, db_path: Path, cache_ttl_hours: float = 24) -> None:
        self.db_path = Path(db_path)
        self.cache_ttl_seconds = cache_ttl_hours * 3600
        self.logger = get_logger("db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Cannot open database {self.db_path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function("normalize_text", 1, normalize_for_comparison, deterministic=True)
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            self.logger.error("Database operation failed: %s", exc)
            raise DatabaseError(f"Database operation failed: {exc}") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_works(self, records: Iterable[WorkRecord]) -> int:
        works = {record.work_key: record for record in records}
        if not works:
            return 0
        now = utc_now()
        authors = {a.author_key: a.name for work in works.values() for a in work.authors}
        work_rows = [
            (
                w.work_key,
                w.title,
                w.first_publish_year,
                w.edition_count,
                ",".join(w.languages),
                w.isbn,
                "|".join(w.subjects),
                w.source,
                now,
                now,
            )
            for w in works.values()
        ]
        with_authors = [w for w in works.values() if w.authors]
        links = [(w.work_key, a.author_key) for w in with_authors for a in w.authors]

        with self.connect() as conn:
            conn.executemany(UPSERT_AUTHOR, [(key, name, now) for key, name in authors.items()])
            conn.executemany(UPSERT_WORK, work_rows)
            conn.executemany(
                "DELETE FROM work_authors WHERE work_key = ?", [(w.work_key,) for w in with_authors]
            )
            conn.executemany(
                "INSERT OR IGNORE INTO work_authors (work_key, author_key) VALUES (?, ?)", links
            )
        self.logger.info("Upserted %d works and %d authors", len(works), len(authors))
        return len(works)

    def search_works(self, term: str, field: str, limit: int = 25) -> list[dict[str, Any]]:
        if field not in SEARCH_FIELDS:
            raise DatabaseError(f"Unsupported search field: {field}")
        value = term if field == "isbn" else f"%{normalize_for_comparison(term)}%"
        query = SEARCH_SELECT.format(condition=SEARCH_CONDITIONS[field])
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, (value, limit))]

    def get_work(self, work_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM works WHERE work_key = ?", (work_key,)).fetchone()
        return dict(row) if row else None

    def fetch_table(self, table: str) -> list[dict[str, Any]]:
        if table not in TABLES:
            raise DatabaseError(f"Unknown table: {table}")
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]

    def row_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in TABLES
            }

    def storage_size_bytes(self) -> int:
        candidates = [self.db_path, *(Path(f"{self.db_path}{s}") for s in ("-wal", "-journal"))]
        return sum(path.stat().st_size for path in candidates if path.exists())

    def get_cached(self, key: str) -> Any | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM api_cache WHERE cache_key = ? AND expires_at > ?",
                (key, time.time()),
            ).fetchone()
            if row is None:
                conn.execute("UPDATE cache_stats SET misses = misses + 1 WHERE id = 1")
                return None
            conn.execute("UPDATE api_cache SET hits = hits + 1 WHERE cache_key = ?", (key,))
            conn.execute("UPDATE cache_stats SET hits = hits + 1 WHERE id = 1")
            return json.loads(row["payload"])

    def set_cache(self, key: str, payload: Any, ttl_seconds: float | None = None) -> None:
        now = time.time()
        ttl = self.cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (cache_key, payload, created_at, expires_at, hits)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload,
                    created_at = excluded.created_at, expires_at = excluded.expires_at, hits = 0
                """,
                (key, json.dumps(payload, ensure_ascii=False), now, now + ttl),
            )

    def set_cache_many(self, items: dict[str, Any], ttl_seconds: float | None = None) -> int:
        now = time.time()
        ttl = self.cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        rows = [
            (key, json.dumps(value, ensure_ascii=False), now, now + ttl)
            for key, value in items.items()
        ]
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO api_cache "
                "(cache_key, payload, created_at, expires_at, hits) VALUES (?, ?, ?, ?, 0)",
                rows,
            )
        return len(rows)

    def purge_expired_cache(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM api_cache WHERE expires_at <= ?", (time.time(),))
            return cursor.rowcount

    def clear_cache(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM api_cache")
            conn.execute("UPDATE cache_stats SET hits = 0, misses = 0 WHERE id = 1")
            return cursor.rowcount

    def cache_stats(self) -> dict[str, Any]:
        now = time.time()
        with self.connect() as conn:
            stats = conn.execute("SELECT hits, misses FROM cache_stats WHERE id = 1").fetchone()
            active = conn.execute(
                "SELECT COUNT(*) FROM api_cache WHERE expires_at > ?", (now,)
            ).fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM api_cache WHERE expires_at <= ?", (now,)
            ).fetchone()[0]
        hits, misses = stats["hits"], stats["misses"]
        lookups = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "lookups": lookups,
            "hit_ratio": round(hits / lookups * 100, 2) if lookups else 0.0,
            "active_entries": active,
            "expired_entries": expired,
        }

    def vacuum(self) -> None:
        with self.connect() as conn:
            conn.execute("VACUUM")
