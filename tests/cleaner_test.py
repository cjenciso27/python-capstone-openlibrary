import pytest

from src.exceptions import DataValidationError
from src.processing.cleaner import (
    clean_query,
    clean_subject,
    clean_text,
    extract_isbns,
    is_valid_isbn,
    is_valid_isbn10,
    is_valid_isbn13,
    isbn10_to_isbn13,
    normalize_for_comparison,
    normalize_isbn,
    normalize_language,
    parse_isbn,
    parse_year,
    smart_title,
    split_values,
    to_isbn13,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  The   Hobbit \n", "The Hobbit"),
        ("Tab\tseparated\x00text", "Tab separatedtext"),
        ("\ufeffBOM title\u200b", "BOM title"),
        ("ﬁction", "fiction"),  # NFKC ligature normalization
        (None, ""),
        (1984, "1984"),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Cien Años de Soledad!", "cien anos de soledad"),
        ("Moby-Dick", "moby dick"),
        ("  GABRIEL   García  Márquez ", "gabriel garcia marquez"),
    ],
)
def test_normalize_for_comparison(raw, expected):
    assert normalize_for_comparison(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("the hobbit", "The Hobbit"), ("DUNE", "Dune"), ("iPhone Stories", "iPhone Stories")],
)
def test_smart_title_only_fixes_single_case_text(raw, expected):
    assert smart_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Science Fiction!!", "science_fiction"),
        ("  Ciencia-Ficción  ", "ciencia_ficcion"),
        ("love___stories", "love_stories"),
    ],
)
def test_clean_subject(raw, expected):
    assert clean_subject(raw) == expected


@pytest.mark.parametrize("raw", ["!!!", "", "x" * 61])
def test_clean_subject_rejects_invalid(raw):
    with pytest.raises(DataValidationError):
        clean_subject(raw)


def test_clean_query_strips_dangerous_characters():
    assert clean_query("  <script>Tolkien</script>; DROP  ") == "script Tolkien script DROP"
    assert clean_query("Ursula K. Le Guin") == "Ursula K. Le Guin"


def test_clean_query_enforces_min_length_and_truncates():
    with pytest.raises(DataValidationError):
        clean_query("@")
    assert len(clean_query("a" * 500)) == 120


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ISBN-13: 978-0-14-032872-1", "9780140328721"), ("0 14 032872 6", "0140328726")],
)
def test_normalize_isbn(raw, expected):
    assert normalize_isbn(raw) == expected


@pytest.mark.parametrize("isbn", ["0140328726", "0306406152", "080442957X"])
def test_valid_isbn10(isbn):
    assert is_valid_isbn10(isbn)


@pytest.mark.parametrize("isbn", ["0140328727", "014032872", "ABCDEFGHIJ"])
def test_invalid_isbn10(isbn):
    assert not is_valid_isbn10(isbn)


@pytest.mark.parametrize("isbn", ["9780140328721", "9780306406157", "9780547928227"])
def test_valid_isbn13(isbn):
    assert is_valid_isbn13(isbn)


@pytest.mark.parametrize("isbn", ["9780140328722", "1234567890123", "978014032872"])
def test_invalid_isbn13(isbn):
    assert not is_valid_isbn13(isbn)


def test_isbn10_to_isbn13_conversion():
    assert isbn10_to_isbn13("0306406152") == "9780306406157"
    assert isbn10_to_isbn13("0-14-032872-6") == "9780140328721"
    assert to_isbn13("080442957X") == "9780804429573"


def test_to_isbn13_rejects_invalid():
    with pytest.raises(DataValidationError):
        to_isbn13("1234")


def test_is_valid_isbn_accepts_formatted_input():
    assert is_valid_isbn("ISBN 0-306-40615-2")
    assert not is_valid_isbn("not an isbn")


def test_parse_isbn_returns_isbn13():
    assert parse_isbn("0-14-032872-6") == "9780140328721"
    assert parse_isbn("978-0-14-032872-1") == "9780140328721"


@pytest.mark.parametrize(
    ("raw", "message"),
    [("12345", "10 or 13 digits"), ("0140328727", "ISBN-10 checksum"), ("9780140328722", "ISBN-13")],
)
def test_parse_isbn_errors(raw, message):
    with pytest.raises(DataValidationError, match=message):
        parse_isbn(raw)


def test_extract_isbns_from_messy_text():
    text = "Editions: 0451524934 / 978-0451524935; bad 9780451524936; dup 0-451-52493-4"
    assert extract_isbns(text) == ["0451524934", "9780451524935"]
    assert extract_isbns("unknown") == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1949, 1949),
        ("c. 1937", 1937),
        ("June 1960", 1960),
        ("1813-01-28", 1813),
        ("unknown", None),
        ("3020", None),
        (None, None),
        (True, None),
    ],
)
def test_parse_year(raw, expected):
    assert parse_year(raw) == expected


def test_split_values_deduplicates_and_cleans():
    assert split_values("Fantasy; Adventure | fantasy ,Fantasy") == ["Fantasy", "Adventure", "fantasy"]
    assert split_values(["a", " a ", "", "b"]) == ["a", "b"]
    assert split_values(None) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("English", "eng"), ("es", "spa"), ("Español", "spa"), ("Français", "fre"), ("tur", "tur")],
)
def test_normalize_language(raw, expected):
    assert normalize_language(raw) == expected
