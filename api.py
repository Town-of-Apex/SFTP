"""Minimal HTTP API for Power Automate–triggered Everbridge deletes.

No authentication yet — restrict network access (VPN / firewall / private IP)
until auth is added.

Endpoints
---------
GET  /health
DELETE /contacts/{employee_id}
POST /delete   {"employeeId": "1234"}
"""

from __future__ import annotations

import logging
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.config import load_config
from src.delete_service import (
    DeleteTransportError,
    MasterUnavailableError,
    delete_external_id,
)
from src.logging_config import setup_logging

logger = logging.getLogger("everbridge-sync.api")

app = FastAPI(
    title="Everbridge Delete API",
    description="On-demand contact delete for Power Automate / Teams workflows.",
    version="0.1.0",
)


class DeleteRequest(BaseModel):
    employeeId: str = Field(..., min_length=1, description="Everbridge External ID")


class DeleteResponse(BaseModel):
    status: str
    employeeId: str
    contact: str
    archive: str | None = None
    stateEntriesRemoved: int = 0


def _run_delete(employee_id: str) -> DeleteResponse:
    config = load_config()
    try:
        result = delete_external_id(config, employee_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MasterUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DeleteTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return DeleteResponse(
        status="deleted",
        employeeId=result.employee_id,
        contact=result.contact_name,
        archive=result.archive_path,
        stateEntriesRemoved=result.state_entries_removed,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.delete("/contacts/{employee_id}", response_model=DeleteResponse)
def delete_contact(employee_id: str) -> DeleteResponse:
    """Delete an Everbridge contact by External ID (employee ID)."""
    logger.info("DELETE /contacts/%s", employee_id)
    return _run_delete(employee_id)


@app.post("/delete", response_model=DeleteResponse)
def delete_contact_post(body: DeleteRequest) -> DeleteResponse:
    """Same as DELETE /contacts/{id}; JSON body for Power Automate convenience."""
    logger.info("POST /delete employeeId=%s", body.employeeId)
    return _run_delete(body.employeeId)


def main() -> None:
    setup_logging()
    host = os.getenv("DELETE_API_HOST", "0.0.0.0")
    port = int(os.getenv("DELETE_API_PORT", "8080"))
    logger.info("Starting delete API on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
