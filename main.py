"""CLI entry point for manual sync runs."""

from src.logging_config import setup_logging
from src.pipeline import run_sync


def main() -> None:
    setup_logging()
    run_sync()


if __name__ == "__main__":
    main()
