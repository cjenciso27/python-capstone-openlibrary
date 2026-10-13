import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "library"
FILE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
CONSOLE_FORMAT = "  [%(levelname)s] %(message)s"


class ConsoleFormatter(logging.Formatter):
    """Formats only the message so tracebacks stay in the log file, not the terminal UI."""

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        return self.formatMessage(record)


def setup_logging(
    log_dir: Path,
    level: str = "INFO",
    console_level: str = "WARNING",
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ConsoleFormatter(CONSOLE_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
