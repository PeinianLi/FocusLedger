from __future__ import annotations

import logging

from .config import AppConfig


def configure_logging(config: AppConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=config.log_path,
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
