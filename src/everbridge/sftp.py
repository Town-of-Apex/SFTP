"""SFTP-based Everbridge contact transport."""

from __future__ import annotations

import csv
import logging
import os
import time

import paramiko

from src.config import Config
from src.delta import load_master_headers, write_delete_staging_csv
from src.everbridge.protocol import EverbridgeTransportError

logger = logging.getLogger("everbridge-sync.everbridge.sftp")


class SftpTransport:
    def __init__(self, config: Config) -> None:
        self._config = config

    def _upload_file(self, local_path: str, remote_path: str) -> None:
        if not os.path.exists(local_path):
            raise EverbridgeTransportError(f"Staging file not found: {local_path}")

        local_size = os.path.getsize(local_path)
        with open(local_path, encoding="utf-8") as handle:
            row_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)

        logger.info(
            "Connecting to Everbridge SFTP at %s:%s...",
            self._config.sftp_host,
            self._config.sftp_port,
        )

        last_error: Exception | None = None
        for attempt in range(1, self._config.sftp_max_retries + 1):
            transport = None
            sftp = None
            try:
                key = paramiko.RSAKey.from_private_key_file(self._config.sftp_key_path)
                transport = paramiko.Transport(
                    (self._config.sftp_host, self._config.sftp_port)
                )
                transport.banner_timeout = self._config.sftp_timeout_seconds
                transport.connect(username=self._config.sftp_username, pkey=key)
                sftp = paramiko.SFTPClient.from_transport(transport)

                logger.info(
                    "Uploading %s row(s) (%s bytes) to %s (attempt %s/%s)...",
                    row_count,
                    local_size,
                    remote_path,
                    attempt,
                    self._config.sftp_max_retries,
                )
                sftp.put(local_path, remote_path)

                remote_attrs = sftp.stat(remote_path)
                if remote_attrs.st_size != local_size:
                    raise EverbridgeTransportError(
                        "Remote file size mismatch: "
                        f"local={local_size}, remote={remote_attrs.st_size}"
                    )

                logger.info("Everbridge SFTP upload complete.")
                return
            except EverbridgeTransportError:
                raise
            except Exception as exc:
                last_error = exc
                logger.error(
                    "SFTP upload failed (attempt %s/%s): %s",
                    attempt,
                    self._config.sftp_max_retries,
                    exc,
                )
                if attempt < self._config.sftp_max_retries:
                    time.sleep(2 ** attempt)
            finally:
                if sftp is not None:
                    sftp.close()
                if transport is not None:
                    transport.close()

        raise EverbridgeTransportError(
            str(last_error) if last_error else "Unknown SFTP error"
        )

    def upsert_batch(self, staging_csv_path: str) -> None:
        remote_path = (
            f"{self._config.sftp_remote_dir}/{self._config.sftp_remote_filename}"
        )
        self._upload_file(staging_csv_path, remote_path)

    def delete_contact(self, external_id: str) -> bool:
        if not external_id.strip():
            raise EverbridgeTransportError("External ID is required for delete.")

        try:
            headers = load_master_headers(self._config)
        except FileNotFoundError as exc:
            raise EverbridgeTransportError(
                f"Master CSV not found at '{self._config.local_master_copy}'. "
                "Run a sync first or download the master CSV before deleting."
            ) from exc
        except ValueError as exc:
            raise EverbridgeTransportError(str(exc)) from exc

        write_delete_staging_csv(self._config, headers, external_id.strip())
        remote_path = (
            f"{self._config.sftp_remote_delete_dir}/"
            f"{self._config.sftp_remote_delete_filename}"
        )
        self._upload_file(self._config.delete_staging_csv, remote_path)
        return True
