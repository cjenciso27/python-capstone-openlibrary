import asyncio
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from src.api.async_client import AsyncLibraryClient
from src.api.sync_client import SyncLibraryClient
from src.cli.ui import (
    ProgressBar,
    format_bytes,
    format_key_values,
    format_table,
    rule,
)
from src.config import (
    BASE_DIR,
    ENV_FILE,
    Settings,
    editable_fields,
    get_settings,
    read_env_file,
    reload_settings,
    update_env_value,
)
from src.db.manager import DatabaseManager
from src.exceptions import APIError, DataValidationError, LibraryAppError, NotFoundError
from src.logger import get_logger, setup_logging
from src.models import WorkRecord
from src.processing.analytics import DataProcessor
from src.processing.catalog import CatalogProcessor
from src.processing.cleaner import clean_query, clean_subject, normalize_for_comparison, parse_isbn
from src.reporting.generator import KPI_LABELS, ReportGenerator

T = TypeVar("T")

APP_TITLE = "OPEN LIBRARY INTELLIGENCE & CATALOG AUTOMATION SYSTEM"
DEFAULT_CATALOG = BASE_DIR / "data" / "sample_catalog.csv"
DEFAULT_PAGES = 3
MAX_PAGES = 20
SEARCH_LIMIT = 25
MAX_ATTEMPTS = 3
MAX_ERRORS_SHOWN = 10

RESULT_COLUMNS = [
    ("title", "Title"),
    ("authors", "Authors"),
    ("first_publish_year", "Year"),
    ("edition_count", "Editions"),
    ("isbn", "ISBN-13"),
    ("work_key", "Work Key"),
]
SEARCH_MODES = {
    "1": ("author", "Author name"),
    "2": ("title", "Work title"),
    "3": ("isbn", "ISBN-10 / ISBN-13"),
}


@dataclass
class BenchmarkResult:
    mode: str
    seconds: float
    requests: int
    works: int


@dataclass
class CheckResult:
    name: str
    command: str
    passed: bool
    seconds: float


def parse_subject_payloads(payloads: list[dict[str, Any]], subject: str) -> list[WorkRecord]:
    records: dict[str, WorkRecord] = {}
    for payload in payloads:
        for item in payload.get("works") or []:
            try:
                record = WorkRecord.from_subject_work(item, subject)
            except DataValidationError:
                continue
            records.setdefault(record.work_key, record)
    return list(records.values())


def parse_search_docs(payload: dict[str, Any], preferred_isbn: str | None = None) -> list[WorkRecord]:
    records: dict[str, WorkRecord] = {}
    for doc in payload.get("docs") or []:
        try:
            record = WorkRecord.from_search_doc(doc, preferred_isbn)
        except DataValidationError:
            continue
        records.setdefault(record.work_key, record)
    return list(records.values())


def search_cache_key(field: str, term: str) -> str:
    return f"search:{field}:{normalize_for_comparison(term)}"


def subject_cache_key(subject: str, limit: int, offset: int) -> str:
    return f"subject:{subject}:{limit}:{offset}"


class CLIApp:
    """Interactive, loop-driven terminal controller for the automation system."""

    def __init__(
        self,
        settings: Settings | None = None,
        db: DatabaseManager | None = None,
        input_func: Callable[[str], str] = input,
        sync_client_factory: Callable[..., SyncLibraryClient] = SyncLibraryClient,
        async_client_factory: Callable[..., AsyncLibraryClient] = AsyncLibraryClient,
        configure_logging: bool = True,
        pause_after_action: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        if configure_logging:
            setup_logging(self.settings.log_dir, self.settings.log_level)
        self.logger = get_logger("cli")
        self.db = db or DatabaseManager(self.settings.db_path, self.settings.cache_ttl_hours)
        self.input_func = input_func
        self.sync_client_factory = sync_client_factory
        self.async_client_factory = async_client_factory
        self.pause_after_action = pause_after_action
        self.running = False
        self.options: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("Ingest Works by Subject", self.ingest_by_subject),
            "2": ("Search Books by Author/Title/ISBN", self.search_books),
            "3": ("Process & Deduplicate Local Catalog File", self.process_catalog),
            "4": ("Generate Analytical Reports", self.generate_reports),
            "5": ("System Health, Cache Maintenance & Database Administration", self.system_admin),
            "6": ("Run Quality Checks & Test Suite Diagnostic", self.quality_checks),
            "0": ("Exit", self.exit_app),
        }

    # ================================================================ main loop
    def run(self) -> int:
        self.running = True
        self.logger.info("Application started")
        while self.running:
            self.show_menu()
            try:
                choice = self.ask("Select an option: ")
            except (EOFError, KeyboardInterrupt):
                print("\n  Session closed. Goodbye!")
                break
            option = self.options.get(choice)
            if option is None:
                print(f"  [!] Invalid option '{choice}'. Please enter a number from 0 to 6.")
                continue
            self.execute(*option)
        self.logger.info("Application stopped")
        return 0

    def execute(self, label: str, handler: Callable[[], None]) -> None:
        try:
            handler()
        except KeyboardInterrupt:
            print("\n  [!] Operation cancelled by the user.")
        except EOFError:
            self.running = False
            return
        except DataValidationError as exc:
            print(f"  [!] Invalid data: {exc}")
        except NotFoundError as exc:
            print(f"  [!] Not found: {exc}")
        except APIError as exc:
            print(f"  [x] Open Library API error: {exc}")
        except LibraryAppError as exc:
            print(f"  [x] {exc}")
        except Exception as exc:
            self.logger.exception("Unexpected error in '%s'", label)
            print(f"  [x] Unexpected error ({type(exc).__name__}): {exc}")
            print(f"      Details were written to {self.settings.log_dir / 'app.log'}")
        if self.running:
            self.pause()

    def show_menu(self) -> None:
        print()
        print(rule())
        print(APP_TITLE)
        print(rule())
        for key in [*sorted(k for k in self.options if k != "0"), "0"]:
            print(f"[{key}] {self.options[key][0]}")
        print(rule())
        try:
            counts = self.db.row_counts()
            print(
                f"  DB: {counts['works']} works | {counts['authors']} authors | "
                f"{counts['api_cache']} cached API responses"
            )
            print(rule("-"))
        except LibraryAppError:
            pass

    def exit_app(self) -> None:
        self.running = False
        print("  Thank you for using the Open Library automation system. Goodbye!")

    # ================================================================ input helpers
    def ask(self, prompt: str) -> str:
        return self.input_func(prompt).strip()

    def pause(self) -> None:
        if self.pause_after_action:
            self.ask("\n  Press Enter to return to the menu...")

    @staticmethod
    def section(title: str) -> None:
        print()
        print(rule("-"))
        print(f"  {title}")
        print(rule("-"))

    def ask_validated(self, prompt: str, parser: Callable[[str], T]) -> T | None:
        """Prompts until the parser accepts the value. Blank input cancels the operation."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw = self.ask(prompt)
            if not raw:
                print("  Operation cancelled.")
                return None
            try:
                return parser(raw)
            except DataValidationError as exc:
                remaining = MAX_ATTEMPTS - attempt
                print(f"  [!] {exc}" + (f" ({remaining} attempt(s) left)" if remaining else ""))
        raise DataValidationError("Too many invalid attempts.")

    def ask_int(self, prompt: str, default: int, minimum: int, maximum: int) -> int:
        def parse(raw: str) -> int:
            if not raw.isdigit():
                raise DataValidationError("Please enter a whole number.")
            value = int(raw)
            if not minimum <= value <= maximum:
                raise DataValidationError(f"Value must be between {minimum} and {maximum}.")
            return value

        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw = self.ask(prompt)
            if not raw:
                return default
            try:
                return parse(raw)
            except DataValidationError as exc:
                print(f"  [!] {exc} ({MAX_ATTEMPTS - attempt} attempt(s) left)")
        raise DataValidationError("Too many invalid attempts.")

    def confirm(self, prompt: str) -> bool:
        return self.ask(f"{prompt} [y/N]: ").lower() in {"y", "yes", "s", "si"}

    # ================================================================ option 1
    def ingest_by_subject(self) -> None:
        self.section("[1] Ingest Works by Subject")
        subject = self.ask_validated("  Subject (e.g. 'science fiction', 'history'): ", clean_subject)
        if subject is None:
            return
        pages = self.ask_int(
            f"  Page depth [1-{MAX_PAGES}] (Enter = {DEFAULT_PAGES}): ",
            DEFAULT_PAGES,
            1,
            MAX_PAGES,
        )
        limit = self.settings.page_size
        print(f"\n  Normalized subject: '{subject}' | {pages} page(s) x {limit} works per page\n")

        start = time.perf_counter()
        sync_payloads = self.fetch_subject_sync(subject, pages, limit)
        sync_seconds = time.perf_counter() - start

        start = time.perf_counter()
        async_payloads = self.fetch_subject_async(subject, pages, limit)
        async_seconds = time.perf_counter() - start

        sync_records = parse_subject_payloads(sync_payloads, subject)
        records = parse_subject_payloads(async_payloads, subject) or sync_records
        results = [
            BenchmarkResult("Sequential (sync)", sync_seconds, pages, len(sync_records)),
            BenchmarkResult("Concurrent (async)", async_seconds, pages, len(records)),
        ]
        self.show_benchmark(results)

        if not records:
            print(f"  [!] Open Library returned no works for subject '{subject}'.")
            return
        offsets = [page * limit for page in range(pages)]
        cached = self.db.set_cache_many(
            {
                subject_cache_key(subject, limit, offset): payload
                for offset, payload in zip(offsets, async_payloads, strict=False)
            }
        )
        stored = self.db.upsert_works(records)
        total = async_payloads[0].get("work_count") if async_payloads else None
        print(f"  [OK] {stored} unique works upserted into SQLite; {cached} API pages cached.")
        if total:
            print(f"       Open Library reports {total:,} works available for this subject.")

    def fetch_subject_sync(self, subject: str, pages: int, limit: int) -> list[dict[str, Any]]:
        with self.sync_client_factory(self.settings) as client:
            return client.fetch_subject_pages(subject, pages, limit, on_page=ProgressBar("Sync"))

    def fetch_subject_async(self, subject: str, pages: int, limit: int) -> list[dict[str, Any]]:
        async def runner() -> list[dict[str, Any]]:
            async with self.async_client_factory(self.settings) as client:
                return await client.fetch_subject_pages(
                    subject, pages, limit, on_page=ProgressBar("Async")
                )

        return asyncio.run(runner())

    @staticmethod
    def show_benchmark(results: list[BenchmarkResult]) -> None:
        rows = [
            {
                "mode": r.mode,
                "seconds": round(r.seconds, 3),
                "requests": r.requests,
                "works": r.works,
                "req_per_sec": round(r.requests / r.seconds, 2) if r.seconds else None,
            }
            for r in results
        ]
        columns = [
            ("mode", "Mode"),
            ("seconds", "Runtime (s)"),
            ("requests", "Requests"),
            ("works", "Works"),
            ("req_per_sec", "Req/s"),
        ]
        print("\n  Runtime comparison")
        print(format_table(rows, columns))
        sync, concurrent = results[0].seconds, results[1].seconds
        if sync and concurrent:
            faster = "async" if concurrent < sync else "sync"
            ratio = max(sync, concurrent) / min(sync, concurrent)
            print(f"  -> The {faster} strategy was {ratio:.2f}x faster.\n")

    # ================================================================ option 2
    def search_books(self) -> None:
        self.section("[2] Search Books by Author / Title / ISBN")
        for key, (_, label) in SEARCH_MODES.items():
            print(f"  [{key}] {label}")
        mode = self.ask("  Search by: ")
        if mode not in SEARCH_MODES:
            raise DataValidationError(f"'{mode}' is not a valid search mode (1-3).")
        field, label = SEARCH_MODES[mode]
        parser = parse_isbn if field == "isbn" else clean_query
        term = self.ask_validated(f"  {label}: ", parser)
        if term is None:
            return
        if field == "isbn":
            print(f"  ISBN checksum valid -> normalized ISBN-13: {term}")

        rows, source = self.lookup(field, term)
        if not rows:
            print(f"  [!] No books found for {field} '{term}'.")
            return
        print(f"\n  {len(rows)} result(s) for {field} '{term}' (source: {source})")
        print(format_table(rows, RESULT_COLUMNS))

    def lookup(self, field: str, term: str) -> tuple[list[dict[str, Any]], str]:
        """Local DB first, then API cache, then the live Open Library API."""
        rows = self.db.search_works(term, field, SEARCH_LIMIT)
        if rows:
            return rows, "local database"

        key = search_cache_key(field, term)
        payload = self.db.get_cached(key)
        source = "API cache"
        if payload is None:
            print("  No local match and cache miss -> querying the Open Library API...")
            with self.sync_client_factory(self.settings) as client:
                payload = client.search(term, field, limit=SEARCH_LIMIT)
            self.db.set_cache(key, payload)
            source = "Open Library API"

        records = parse_search_docs(payload, term if field == "isbn" else None)
        if records:
            self.db.upsert_works(records)
        return [record.as_row() for record in records], source

    # ================================================================ option 3
    def resolve_catalog_path(self, raw: str) -> Path:
        if not raw:
            return DEFAULT_CATALOG
        path = Path(raw.strip().strip("\"'")).expanduser()
        if not path.is_absolute() and not path.exists():
            path = BASE_DIR / path
        return path

    def process_catalog(self) -> None:
        self.section("[3] Process & Deduplicate Local Catalog File")
        default = DEFAULT_CATALOG.relative_to(BASE_DIR)
        raw = self.ask(f"  Catalog file (.csv/.json/.jsonl) [Enter = {default}]: ")
        path = self.resolve_catalog_path(raw)
        print(f"  Streaming '{path.name}' through the cleaning generator...")
        start = time.perf_counter()
        report = CatalogProcessor(self.db).process(path)
        seconds = time.perf_counter() - start

        print(
            format_key_values(
                [
                    ("File", report.file.name),
                    ("Rows read", report.rows_read),
                    ("Invalid rows skipped", report.invalid),
                    ("Duplicates removed", report.duplicates),
                    ("Unique records", report.unique),
                    ("Rows upserted into SQLite", report.upserted),
                    ("Invalid ISBNs discarded", report.isbn_rejected),
                    ("Processing time (s)", round(seconds, 3)),
                ]
            )
        )
        if report.errors:
            print(f"\n  Validation issues (showing {min(len(report.errors), MAX_ERRORS_SHOWN)}):")
            for error in report.errors[:MAX_ERRORS_SHOWN]:
                print(f"   - {error}")

    # ================================================================ option 4
    def generate_reports(self) -> None:
        self.section("[4] Generate Analytical Reports")
        processor = DataProcessor.from_database(self.db)
        if processor.is_empty:
            print("  [!] No works in the database yet. Run option 1, 2 or 3 first.")
            return
        print("  Running the pandas analytics engine...")
        bundle = ReportGenerator(processor, self.settings.output_dir).generate_all()

        print("\n  Key performance metrics")
        print(format_key_values([(label, bundle.kpis.get(k)) for k, label in KPI_LABELS.items()]))
        print("\n  Top publication years & languages")
        print(format_table(DataProcessor.to_records(processor.summary(5))))
        print("\n  Generated files")
        for path in bundle.files:
            print(f"   - {path}  ({format_bytes(path.stat().st_size)})")

    # ================================================================ option 5
    def system_admin(self) -> None:
        actions: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("Show database metrics", self.show_db_metrics),
            "2": ("Purge expired cache entries", self.purge_cache),
            "3": ("Clear the entire API cache", self.clear_cache),
            "4": ("View runtime configuration", self.show_config),
            "5": ("Update a configuration value (.env)", self.update_config),
            "6": ("Reload configuration from .env", self.reload_config),
            "7": ("Optimize database file (VACUUM)", self.vacuum_db),
        }
        while True:
            self.section("[5] System Health, Cache Maintenance & Database Administration")
            for key, (label, _) in actions.items():
                print(f"  [{key}] {label}")
            print("  [0] Back to main menu")
            choice = self.ask("  Select an option: ")
            if choice == "0":
                return
            if choice not in actions:
                print(f"  [!] Invalid option '{choice}'.")
                continue
            try:
                actions[choice][1]()
            except DataValidationError as exc:
                print(f"  [!] {exc}")
            except LibraryAppError as exc:
                print(f"  [x] {exc}")

    def show_db_metrics(self) -> None:
        counts = self.db.row_counts()
        stats = self.db.cache_stats()
        print("\n  Table row counts")
        print(format_table([{"table": t, "rows": n} for t, n in counts.items()]))
        print("\n  Storage & cache")
        print(
            format_key_values(
                [
                    ("Database file", str(self.db.db_path)),
                    ("Total storage size", format_bytes(self.db.storage_size_bytes())),
                    ("Cache TTL (hours)", self.settings.cache_ttl_hours),
                    ("Cache lookups", stats["lookups"]),
                    ("Cache hits", stats["hits"]),
                    ("Cache misses", stats["misses"]),
                    ("Cache hit ratio (%)", stats["hit_ratio"]),
                    ("Active cache entries", stats["active_entries"]),
                    ("Expired cache entries", stats["expired_entries"]),
                ]
            )
        )

    def purge_cache(self) -> None:
        removed = self.db.purge_expired_cache()
        print(f"  [OK] Purged {removed} expired cache entr{'y' if removed == 1 else 'ies'}.")

    def clear_cache(self) -> None:
        if not self.confirm("  This removes ALL cached API responses. Continue?"):
            print("  Cancelled.")
            return
        removed = self.db.clear_cache()
        print(f"  [OK] Removed {removed} cache entries and reset hit/miss statistics.")

    def vacuum_db(self) -> None:
        before = self.db.storage_size_bytes()
        self.db.vacuum()
        after = self.db.storage_size_bytes()
        print(f"  [OK] Database optimized: {format_bytes(before)} -> {format_bytes(after)}.")

    def show_config(self) -> None:
        env_values = read_env_file(ENV_FILE)
        rows = [
            {
                "setting": name,
                "env_key": Settings.env_key(name),
                "active": getattr(self.settings, name),
                "env_file": env_values.get(Settings.env_key(name)),
            }
            for name in editable_fields()
        ]
        status = "found" if ENV_FILE.is_file() else "not found (defaults in use)"
        print(f"\n  .env file: {ENV_FILE} [{status}]")
        columns = [
            ("setting", "Setting"),
            ("env_key", "Variable"),
            ("active", "Active value"),
            ("env_file", ".env value"),
        ]
        print(format_table(rows, columns, max_width=45))

    def update_config(self) -> None:
        fields = editable_fields()
        print()
        for index, name in enumerate(fields, start=1):
            print(f"  [{index:>2}] {name} = {getattr(self.settings, name)}")
        number = self.ask_int("  Setting number to change (Enter = cancel): ", 0, 0, len(fields))
        if number == 0:
            print("  Cancelled.")
            return
        name = fields[number - 1]
        value = self.ask(f"  New value for {name}: ")
        if not value:
            print("  Cancelled.")
            return
        update_env_value(name, value, ENV_FILE)
        print(f"  [OK] {Settings.env_key(name)}={value} saved to {ENV_FILE.name}.")
        self.apply_settings(reload_settings())

    def reload_config(self) -> None:
        self.apply_settings(reload_settings())
        print("  [OK] Configuration reloaded from environment and .env file.")

    def apply_settings(self, settings: Settings) -> None:
        previous = self.settings
        self.settings = settings
        if (settings.db_path, settings.cache_ttl_hours) != (
            previous.db_path,
            previous.cache_ttl_hours,
        ):
            self.db = DatabaseManager(settings.db_path, settings.cache_ttl_hours)
            print(f"  Database manager re-initialized at {settings.db_path}")
        if (settings.log_dir, settings.log_level) != (previous.log_dir, previous.log_level):
            setup_logging(settings.log_dir, settings.log_level)
            print(f"  Logging re-configured (level {settings.log_level}).")

    # ================================================================ option 6
    @staticmethod
    def quality_commands() -> list[tuple[str, list[str]]]:
        uv = shutil.which("uv")
        if uv:
            return [
                ("Ruff lint", [uv, "run", "ruff", "check", "."]),
                ("Format check", [uv, "format", "--check"]),
                ("Pytest suite", [uv, "run", "pytest", "-q", "--color=no"]),
            ]
        python = sys.executable
        return [
            ("Ruff lint", [python, "-m", "ruff", "check", "."]),
            ("Format check", [python, "-m", "ruff", "format", "--check", "."]),
            ("Pytest suite", [python, "-m", "pytest", "-q", "--color=no"]),
        ]

    @staticmethod
    def stream_command(command: list[str]) -> bool:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
        try:
            process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except (FileNotFoundError, PermissionError) as exc:
            print(f"    | Cannot start command: {exc}")
            return False
        assert process.stdout is not None
        for line in process.stdout:
            print(f"    | {line.rstrip()}")
        return process.wait() == 0

    def quality_checks(self) -> None:
        self.section("[6] Run Quality Checks & Test Suite Diagnostic")
        results: list[CheckResult] = []
        for name, command in self.quality_commands():
            display = " ".join(Path(command[0]).stem if i == 0 else p for i, p in enumerate(command))
            print(f"\n  >> {name}: {display}")
            start = time.perf_counter()
            passed = self.stream_command(command)
            results.append(CheckResult(name, display, passed, time.perf_counter() - start))

        rows = [
            {
                "check": r.name,
                "command": r.command,
                "status": "PASS" if r.passed else "FAIL",
                "seconds": round(r.seconds, 2),
            }
            for r in results
        ]
        print("\n  Code health summary")
        print(format_table(rows, max_width=40))
        healthy = all(r.passed for r in results)
        print("  [OK] All quality gates passed." if healthy else "  [x] Some quality gates failed.")
