from dataclasses import dataclass, field
from typing import Any

from src.api.base import strip_key
from src.exceptions import DataValidationError
from src.processing.cleaner import (
    clean_text,
    is_valid_isbn,
    normalize_for_comparison,
    normalize_isbn,
    normalize_language,
    parse_year,
    split_values,
    to_isbn13,
)

MAX_SUBJECTS = 10
MAX_LANGUAGES = 10


def to_int(value: Any, default: int = 0) -> int:
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return default


def first_valid_isbn(values: Any) -> str | None:
    for value in values or []:
        if value and is_valid_isbn(value):
            return to_isbn13(value)
    return None


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    author_key: str
    name: str


@dataclass(slots=True)
class WorkRecord:
    work_key: str
    title: str
    authors: list[AuthorRecord] = field(default_factory=list)
    first_publish_year: int | None = None
    edition_count: int = 0
    languages: list[str] = field(default_factory=list)
    isbn: str | None = None
    subjects: list[str] = field(default_factory=list)
    source: str = "api"

    @property
    def author_names(self) -> str:
        return ", ".join(author.name for author in self.authors)

    @property
    def title_author_key(self) -> str:
        author = self.authors[0].name if self.authors else ""
        return f"{normalize_for_comparison(self.title)}|{normalize_for_comparison(author)}"

    @property
    def dedupe_keys(self) -> set[str]:
        keys = {f"work:{self.work_key}", f"title_author:{self.title_author_key}"}
        if self.isbn:
            keys.add(f"isbn:{self.isbn}")
        return keys

    def as_row(self) -> dict[str, Any]:
        return {
            "work_key": self.work_key,
            "title": self.title,
            "authors": self.author_names,
            "first_publish_year": self.first_publish_year,
            "edition_count": self.edition_count,
            "languages": ",".join(self.languages),
            "isbn": self.isbn,
        }

    @classmethod
    def from_subject_work(cls, data: dict[str, Any], subject: str) -> "WorkRecord":
        key = strip_key(data.get("key"), "/works/")
        title = clean_text(data.get("title"))
        if not key or not title:
            raise DataValidationError("Subject work is missing 'key' or 'title'.")
        authors = [
            AuthorRecord(strip_key(author.get("key"), "/authors/"), clean_text(author.get("name")))
            for author in data.get("authors") or []
            if author.get("key") and clean_text(author.get("name"))
        ]
        availability = data.get("availability") or {}
        return cls(
            work_key=key,
            title=title,
            authors=authors,
            first_publish_year=parse_year(data.get("first_publish_year")),
            edition_count=to_int(data.get("edition_count")),
            isbn=first_valid_isbn([availability.get("isbn")]),
            subjects=split_values(data.get("subject"))[:MAX_SUBJECTS],
            source=f"subject:{subject}",
        )

    @classmethod
    def from_search_doc(
        cls, doc: dict[str, Any], preferred_isbn: str | None = None
    ) -> "WorkRecord":
        key = strip_key(doc.get("key"), "/works/")
        title = clean_text(doc.get("title"))
        if not key or not title:
            raise DataValidationError("Search result is missing 'key' or 'title'.")
        names = doc.get("author_name") or []
        keys = doc.get("author_key") or []
        authors = [
            AuthorRecord(author_key, clean_text(name))
            for author_key, name in zip(keys, names, strict=False)
            if author_key and clean_text(name)
        ]
        isbns = [normalize_isbn(value) for value in doc.get("isbn") or []]
        valid_isbns = {to_isbn13(value) for value in isbns if is_valid_isbn(value)}
        if preferred_isbn and preferred_isbn in valid_isbns:
            isbn = preferred_isbn
        else:
            isbn = first_valid_isbn(isbns)
        languages = [normalize_language(lang) for lang in doc.get("language") or []]
        return cls(
            work_key=key,
            title=title,
            authors=authors,
            first_publish_year=parse_year(doc.get("first_publish_year")),
            edition_count=to_int(doc.get("edition_count")),
            languages=[lang for lang in languages if lang][:MAX_LANGUAGES],
            isbn=isbn,
            subjects=split_values(doc.get("subject"))[:MAX_SUBJECTS],
            source="search",
        )
