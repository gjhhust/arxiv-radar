"""
log_config.py — Centralized logging configuration for arxiv-radar.

Usage in any module:
    from log_config import setup_logging
    setup_logging()  # call once at entry point (main.py, weekly.py, etc.)

Individual modules just do:
    import logging
    logger = logging.getLogger(__name__)
"""

import logging
import sys
from datetime import date
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "data" / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO, log_to_file: bool = True) -> None:
    """Configure logging for the entire application."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handlers = [logging.StreamHandler(sys.stdout)]

    if log_to_file:
        log_file = LOG_DIR / f"{date.today().isoformat()}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        handlers=handlers,
        force=True,
    )

    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
