"""
Logging configuration for Connect Bot
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from ..config import BotSettings


def setup_logging(settings: BotSettings) -> None:
    """Configure logging for the bot"""
    
    log_path = Path(settings.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(fmt)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Reduce noise from libraries
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
