"""Everbridge contact transport abstractions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class EverbridgeTransportError(Exception):
    """Raised when an Everbridge transport operation fails."""


@runtime_checkable
class ContactTransport(Protocol):
    """Interface for pushing contact changes to Everbridge.

  Implementations:
  - SftpTransport (today): batch CSV upload via SFTP
  - ApiTransport (future): REST upsert/delete per contact
  """

    def upsert_batch(self, staging_csv_path: str) -> None:
        """Upload a staged CSV of contact rows (upserts by External ID)."""
        ...

    def delete_contact(self, external_id: str) -> bool:
        """Remove a contact by External ID. Returns True if deleted or not found.

  Not implemented for SFTP yet — reserved for API or SFTP delete-file format.
  """
        ...
