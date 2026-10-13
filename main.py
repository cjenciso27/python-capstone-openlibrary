import sys

from pydantic import ValidationError

from src.cli.menu import CLIApp
from src.exceptions import LibraryAppError


def configure_console() -> None:
    """Avoids UnicodeEncodeError on legacy Windows consoles when printing book metadata."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


def main() -> int:
    configure_console()
    try:
        return CLIApp().run()
    except ValidationError as exc:
        print(f"[x] Invalid configuration in .env or environment:\n{exc}", file=sys.stderr)
        return 1
    except LibraryAppError as exc:
        print(f"[x] Startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
