import sys
from collections.abc import Sequence
from typing import Any

LINE_WIDTH = 70
BAR_WIDTH = 28
EMPTY = "-"

Columns = Sequence[tuple[str, str]]


def rule(char: str = "=") -> str:
    return char * LINE_WIDTH


def truncate(value: Any, width: int) -> str:
    text = EMPTY if value is None or value == "" else str(value)
    return text if len(text) <= width else text[: max(width - 3, 1)] + "..."


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def format_table(
    rows: Sequence[dict[str, Any]], columns: Columns | None = None, max_width: int = 38
) -> str:
    """Renders a list of dicts as an aligned, ASCII-only table."""
    if not rows:
        return "  (no rows to display)"
    columns = columns or [(key, key.replace("_", " ").title()) for key in rows[0]]
    cells = [[truncate(row.get(key), max_width) for key, _ in columns] for row in rows]
    widths = [
        max(len(header), *(len(line[index]) for line in cells))
        for index, (_, header) in enumerate(columns)
    ]
    numeric = [all(_is_number(row.get(key)) or row.get(key) is None for row in rows) for key, _ in columns]

    def render(values: list[str], align_numbers: bool = True) -> str:
        parts = [
            value.rjust(width) if align_numbers and numeric[i] else value.ljust(width)
            for i, (value, width) in enumerate(zip(values, widths, strict=True))
        ]
        return "  | " + " | ".join(parts) + " |"

    separator = "  +-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [separator, render([header for _, header in columns], False), separator]
    lines.extend(render(line) for line in cells)
    lines.append(separator)
    return "\n".join(lines)


def format_key_values(pairs: Sequence[tuple[str, Any]]) -> str:
    rows = [{"metric": label, "value": EMPTY if value is None else value} for label, value in pairs]
    return format_table(rows, [("metric", "Metric"), ("value", "Value")], max_width=50)


def format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.2f} {unit}"
        size /= 1024
    return f"{size:,.2f} GB"


class ProgressBar:
    """Callable progress indicator compatible with the API clients' on_page callbacks."""

    def __init__(self, label: str, unit: str = "pages", width: int = BAR_WIDTH) -> None:
        self.label = label
        self.unit = unit
        self.width = width
        self.finished = False

    def __call__(self, completed: int, total: int) -> None:
        total = max(total, 1)
        filled = int(self.width * completed / total)
        bar = "#" * filled + "-" * (self.width - filled)
        pct = completed / total * 100
        sys.stdout.write(f"\r  {self.label:<6} [{bar}] {completed}/{total} {self.unit} {pct:5.1f}%")
        sys.stdout.flush()
        if completed >= total and not self.finished:
            self.finished = True
            sys.stdout.write("\n")
            sys.stdout.flush()
