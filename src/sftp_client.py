"""Backward-compatible SFTP upload entry point.

Prefer src.everbridge.create_transport() for new code.
"""

from __future__ import annotations

from src.config import Config
from src.everbridge import EverbridgeTransportError, create_transport

# Alias for existing imports and tests.
SftpUploadError = EverbridgeTransportError


def upload_to_everbridge(config: Config) -> None:
    create_transport(config).upsert_batch(config.upload_staging_csv)
