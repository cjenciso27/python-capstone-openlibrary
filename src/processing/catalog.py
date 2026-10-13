import csv
import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.db.manager import DatabaseManager
from src.exceptions import DataValidationError
from src.logger import get_logger
from src.models import AuthorRecord, WorkRecord, to_int
from src.processing.cleaner import (
    AUTHOR_SPLIT_RE,
    clean_text,
    extract_isbns,
    normalize_for_comparison,
    normalize_language,
    parse_year,
    smart_title,
    split_values,
    to_isbn13,
)

SUPPORTED_EXTENSIONS = (".csv", ".json", ".jsonl")
FIELD_ALIASES = {
    "title": ("title", "book_title", "name"),
    "author": ("author", "authors", "author_name", "writer"),
    "isbn": ("isbn", "isbn13", "isbn_13", "isbn10", "isbn_10"),
    "year": ("first_publish_year", "year", "publish_year", "publication_year", "published"),
    "language": ("language", "languages", "lang"),
    "subjects": ("subjects", "subject", "genre", "genres"),
    "editions": ("edition_count", "editions"),
}


@dataclass
class CatalogReport:
    file: Path
    rows_read: int = 0
    invalid: int = 0
    duplicates: int = 0
    unique: int = 0
    upserted: int = 0
    isbn_rejected: int = 0
    errors: list[str] = field(default_factory=list)


def local_key(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"LOCAL-{prefix}-{digest}"


class CatalogProcessor:
    def __init__(self, db: DatabaseManager, batch_size: int = 250) -> None:
        self.db = db
        self.batch_size = batch_size
        self.logger = get_logger("catalog")

    def stream_rows(self, path: Path) -> Iterator[dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise DataValidationError(f"Unsupported file type '{suffix}'. Use CSV or JSON.")

        if suffix == ".csv":
            with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
                yield from csv.DictReader(handle)
        elif suffix == ".jsonl":
            with path.open(encoding="utf-8-sig", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DataValidationError(f"Invalid JSON on line {number}") from exc
        else:
            try:
                with path.open(encoding="utf-8-sig", errors="replace") as handle:
                    data = json.load(handle)
            except json.JSONDecodeError as exc:
                raise DataValidationError(f"Invalid JSON file: {exc}") from exc
            items = data.get("works", data.get("books")) if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise DataValidationError("JSON catalog must be a list or contain a 'works' list.")
            yield from items

    @staticmethod
    def pick(row: dict[str, Any], name: str) -> Any:
        normalized = {clean_text(k).lower(): v for k, v in row.items() if k is not None}
        for alias in FIELD_ALIASES[name]:
            value = normalized.get(alias)
            if value not in (None, "", []):
                return value
        return None

    def normalize_row(self, row: Any, report: CatalogReport | None = None) -> WorkRecord:
        if not isinstance(row, dict):
            raise DataValidationError("Entry is not a key/value object.")
        title = smart_title(self.pick(row, "title"))
        if not title:
            raise DataValidationError("Missing title.")

        authors = [
            AuthorRecord(local_key("A", normalize_for_comparison(name)), smart_title(name))
            for name in split_values(self.pick(row, "author"), AUTHOR_SPLIT_RE)
        ]
        raw_isbn = self.pick(row, "isbn")
        isbns = extract_isbns(raw_isbn)
        if raw_isbn and not isbns and report is not None:
            report.isbn_rejected += 1
        languages = [normalize_language(lang) for lang in split_values(self.pick(row, "language"))]

        record = WorkRecord(
            work_key="",
            title=title,
            authors=authors,
            first_publish_year=parse_year(self.pick(row, "year")),
            edition_count=to_int(self.pick(row, "editions"), default=1),
            languages=[lang for lang in languages if lang],
            isbn=to_isbn13(isbns[0]) if isbns else None,
            subjects=[smart_title(s) for s in split_values(self.pick(row, "subjects"))],
            source="catalog",
        )
        record.work_key = local_key("W", record.title_author_key)
        return record

    def validated(self, rows: Iterable[Any], report: CatalogReport) -> Iterator[WorkRecord]:
        for number, row in enumerate(rows, start=1):
            report.rows_read += 1
            try:
                yield self.normalize_row(row, report)
            except DataValidationError as exc:
                report.invalid += 1
                report.errors.append(f"Row {number}: {exc}")

    @staticmethod
    def deduplicate(records: Iterable[WorkRecord], report: CatalogReport) -> Iterator[WorkRecord]:
        seen: set[str] = set()
        for record in records:
            keys = record.dedupe_keys
            if keys & seen:
                report.duplicates += 1
                continue
            seen |= keys
            report.unique += 1
            yield record

    def process(self, path: Path | str) -> CatalogReport:
        path = Path(path)
        if not path.is_file():
            raise DataValidationError(f"Catalog file not found: {path}")

        report = CatalogReport(file=path)
        batch: list[WorkRecord] = []
        for record in self.deduplicate(self.validated(self.stream_rows(path), report), report):
            batch.append(record)
            if len(batch) >= self.batch_size:
                report.upserted += self.db.upsert_works(batch)
                batch.clear()
        if batch:
            report.upserted += self.db.upsert_works(batch)

        self.logger.info(
            "Catalog %s: read=%d invalid=%d duplicates=%d upserted=%d",
            path.name,
            report.rows_read,
            report.invalid,
            report.duplicates,
            report.upserted,
        )
        return report
