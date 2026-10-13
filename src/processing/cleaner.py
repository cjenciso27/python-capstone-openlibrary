import re
import string
import unicodedata
from datetime import datetime
from typing import Any

from src.exceptions import DataValidationError

WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff\u200b]")
PUNCTUATION_RE = re.compile(r"[^\w\s]")
SUBJECT_INVALID_RE = re.compile(r"[^a-z0-9\s_-]")
SUBJECT_SEPARATOR_RE = re.compile(r"[\s_-]+")
QUERY_INVALID_RE = re.compile(r"[^\w\s'.,&:-]")
ISBN_PREFIX_RE = re.compile(r"^ISBN(?:-1[03])?:?", re.IGNORECASE)
ISBN_SEPARATOR_RE = re.compile(r"[\s-]")
ISBN_CANDIDATE_RE = re.compile(
    r"(?<![\dXx])(?:\d[\s-]?){12}\d(?![\dXx])|(?<![\dXx])(?:\d[\s-]?){9}[\dXx](?![\dXx])"
)
ISBN10_RE = re.compile(r"^\d{9}[\dX]$")
ISBN13_RE = re.compile(r"^97[89]\d{10}$")
YEAR_RE = re.compile(r"(?<!\d)(1\d{3}|20\d{2})(?!\d)")
LIST_SPLIT_RE = re.compile(r"\s*[;|,]\s*")
AUTHOR_SPLIT_RE = re.compile(r"\s*[;|]\s*")

LANGUAGE_ALIASES = {
    "en": "eng", "english": "eng", "ingles": "eng",
    "es": "spa", "spanish": "spa", "espanol": "spa", "castellano": "spa",
    "fr": "fre", "french": "fre", "francais": "fre", "frances": "fre",
    "de": "ger", "german": "ger", "deutsch": "ger", "aleman": "ger",
    "it": "ita", "italian": "ita", "italiano": "ita",
    "pt": "por", "portuguese": "por", "portugues": "por",
    "ru": "rus", "russian": "rus",
    "ja": "jpn", "japanese": "jpn",
    "zh": "chi", "chinese": "chi",
}  # fmt: skip


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = CONTROL_RE.sub("", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def remove_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_for_comparison(value: Any) -> str:
    text = remove_accents(clean_text(value)).casefold()
    text = PUNCTUATION_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def smart_title(value: Any) -> str:
    text = clean_text(value)
    if text and (text.islower() or text.isupper()):
        return string.capwords(text.lower())
    return text


def clean_subject(raw: Any) -> str:
    text = SUBJECT_INVALID_RE.sub("", remove_accents(clean_text(raw)).lower())
    text = SUBJECT_SEPARATOR_RE.sub("_", text).strip("_")
    if not text:
        raise DataValidationError("Subject must contain at least one letter or number.")
    if len(text) > 60:
        raise DataValidationError("Subject is too long (max 60 characters).")
    return text


def clean_query(raw: Any, max_length: int = 120) -> str:
    text = WHITESPACE_RE.sub(" ", QUERY_INVALID_RE.sub(" ", clean_text(raw)))
    text = text.strip(" .,:-&'")[:max_length].strip()
    if len(text) < 2:
        raise DataValidationError("Search term must contain at least 2 valid characters.")
    return text


def normalize_isbn(raw: Any) -> str:
    text = ISBN_PREFIX_RE.sub("", clean_text(raw))
    return ISBN_SEPARATOR_RE.sub("", text).upper()


def is_valid_isbn10(isbn: str) -> bool:
    if not ISBN10_RE.match(isbn):
        return False
    total = sum(
        (10 - index) * (10 if char == "X" else int(char)) for index, char in enumerate(isbn)
    )
    return total % 11 == 0


def is_valid_isbn13(isbn: str) -> bool:
    if not ISBN13_RE.match(isbn):
        return False
    total = sum(int(char) * (3 if index % 2 else 1) for index, char in enumerate(isbn))
    return total % 10 == 0


def is_valid_isbn(raw: Any) -> bool:
    isbn = normalize_isbn(raw)
    return is_valid_isbn10(isbn) or is_valid_isbn13(isbn)


def isbn10_to_isbn13(isbn10: str) -> str:
    core = "978" + normalize_isbn(isbn10)[:9]
    total = sum(int(char) * (3 if index % 2 else 1) for index, char in enumerate(core))
    return core + str((10 - total % 10) % 10)


def to_isbn13(raw: Any) -> str:
    isbn = normalize_isbn(raw)
    if is_valid_isbn13(isbn):
        return isbn
    if is_valid_isbn10(isbn):
        return isbn10_to_isbn13(isbn)
    raise DataValidationError(f"Invalid ISBN: {raw}")


def parse_isbn(raw: Any) -> str:
    """Validates user-provided ISBN input and returns its ISBN-13 form."""
    isbn = normalize_isbn(raw)
    if not (ISBN10_RE.match(isbn) or re.fullmatch(r"\d{13}", isbn)):
        raise DataValidationError("ISBN must contain 10 or 13 digits (ISBN-10 may end in X).")
    if len(isbn) == 10 and not is_valid_isbn10(isbn):
        raise DataValidationError(f"ISBN-10 checksum failed for {isbn}.")
    if len(isbn) == 13 and not is_valid_isbn13(isbn):
        raise DataValidationError(f"ISBN-13 checksum failed for {isbn}.")
    return to_isbn13(isbn)


def extract_isbns(value: Any) -> list[str]:
    found: list[str] = []
    for match in ISBN_CANDIDATE_RE.finditer(clean_text(value)):
        candidate = normalize_isbn(match.group())
        if is_valid_isbn(candidate) and candidate not in found:
            found.append(candidate)
    return found


def parse_year(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    match = YEAR_RE.search(clean_text(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if year <= datetime.now().year + 1 else None


def split_values(value: Any, pattern: re.Pattern[str] = LIST_SPLIT_RE) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list | tuple | set) else pattern.split(clean_text(value))
    result: list[str] = []
    for item in items:
        text = clean_text(item)
        if text and text not in result:
            result.append(text)
    return result


def normalize_language(value: Any) -> str:
    text = normalize_for_comparison(value)
    return LANGUAGE_ALIASES.get(text, text)
