# Everbridge Emergency Alert Sync

Automates incremental upload of emergency alert registration data from a Microsoft Form master CSV (OneDrive) to Everbridge via SFTP.

## End-to-End Flow

```
MS Form  →  Power Automate  →  Master CSV (OneDrive)
                                      ↓
                         This app (Docker, scheduled)
                                      ↓
                    ┌─────────────────┴─────────────────┐
                    │                                   │
              Opted In = TRUE                     Opted In = FALSE
                    │                                   │
              Everbridge SFTP                     Everbridge SFTP
              (/update)                           (/delete)
```

1. Staff authenticate and submit the Microsoft Form (opt in or opt out). Every submission — including opt-outs — is appended to the master CSV with the submitter's External ID.
2. Power Automate appends each submission as a new row, including analytics metadata columns after `END` (`Opted In`, `Submitter Email`, `Submitter Department`, `Submission Datetime`).
3. This application runs on a schedule, downloads the master CSV, uses **last-write-wins per External ID**, and uploads only the delta:
   - `Opted In=TRUE` → UPDATE file to Everbridge `/update`
   - `Opted In=FALSE` → DELETE file to Everbridge `/delete`
4. Everbridge upserts or removes contacts by **External ID**. After success, all current master-row signatures for that ID are sealed so older opt-ins cannot resurrect a contact.

## Features

- **Incremental sync** — SHA256 row signatures; only new or changed latest rows per External ID are actioned
- **Opt-in and opt-out** — same sync run builds UPDATE and DELETE batches from `Opted In`
- **Safe state commits** — `sync_state.json` is updated only after successful SFTP upload(s); form opt-outs seal prior signatures instead of purging
- **OneDrive via MS Graph** — delegated auth with MSAL token cache; download by file ID with path fallback
- **Validation** — rejects bad rows to `rejected_rows.csv` without blocking valid ones (opt-outs require External ID only)
- **Failure handling** — failed batches preserved in `failed_uploads/`; Teams/email alerts
- **Docker deployment** — scheduled via APScheduler; manual and admin CLI tools included

## Project Layout

```
main.py              # Manual sync entry point
scheduler.py         # Scheduled daemon (--run-now for ad-hoc)
admin.py             # status, preview, replay, reset-external-id, delete-external-id
api.py               # HTTP delete API for Power Automate (no auth yet)
explore_onedrive.py  # IT tool for Graph / drive discovery
src/
  config.py          # Environment configuration
  graph_client.py    # MS Graph download
  delta.py           # Delta detection and state
  delete_service.py  # Shared Everbridge delete (CLI + API)
  everbridge/        # Contact transport abstraction (SFTP today, API planned)
  validation.py      # Row validation
  sftp_client.py     # Backward-compatible SFTP upload wrapper
  notifications.py   # Failure alerts
  pipeline.py        # Orchestration
docs/
  VALIDATION_RUNBOOK.md
  BULK_LOAD_STRATEGY.md
  FUTURE_ARCHITECTURE.md
ROADMAP.md           # Go-live task checklist
```

## Prerequisites

- Docker and Docker Compose on the host
- `Apex.key` — RSA private key for Everbridge SFTP (mount as volume, never commit)
- Entra app registration with delegated `Files.Read.All` (and admin consent if required)
- One-time browser sign-in on the host (`explore_onedrive.py --device-login`) before Docker deploy
- Master CSV maintained by Power Automate on the signed-in user's OneDrive

## Setup

1. Copy environment template and fill in values:

   ```bash
   cp .env.example .env
   ```

2. Initialize state file (first run only):

   ```bash
   cp sync_state.json.example sync_state.json
   ```

3. Place `Apex.key` in the project root.

4. Configure `.env` (see [`.env.example`](.env.example)) and sign in once on the **host** (browser required):

   ```bash
   uv run explore_onedrive.py --device-login
   ```

   This creates `ms_graph_token_cache.json` for unattended token refresh. The container mounts this file read/write.

5. Build and start:

   ```bash
   docker compose up -d --build
   ```

6. Verify Graph connectivity inside the container:

   ```bash
   docker compose exec sftp-uploader uv run python explore_onedrive.py --skip-file-check
   ```

## Configuration

All settings are via environment variables (see [`.env.example`](.env.example)).

| Variable | Purpose |
|----------|---------|
| `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | Entra app (confidential client) |
| `MS_TOKEN_CACHE_PATH` | MSAL cache file (default: `ms_graph_token_cache.json`) |
| `MS_FILE_ID` | OneDrive item ID for master CSV (primary download target) |
| `MS_FILE_PATH` | Path fallback if ID download fails |
| `MS_REFRESH_TOKEN` | Optional; cache file is preferred |
| `EVERBRIDGE_TRANSPORT` | `sftp` (default). `api` reserved for future REST transport |
| `SFTP_HOST`, `SFTP_PORT`, `SFTP_USERNAME`, `SFTP_KEY_PATH` | Everbridge SFTP |
| `SFTP_REMOTE_DIR`, `SFTP_REMOTE_FILENAME` | Remote upload path |
| `SKIP_GRAPH_DOWNLOAD` | Dev-only: skip OneDrive entirely; use local CSV (default: `false`) |
| `ALLOW_LOCAL_FALLBACK` | Dev-only: use local CSV when Graph fails (default: `false`) |
| `LOCAL_FALLBACK_CSV`, `LOCAL_MASTER_COPY` | Local test source and working copy paths |
| `SYNC_DAY_OF_WEEK`, `SYNC_HOUR`, `SYNC_MINUTE`, `SYNC_TIMEZONE` | Schedule (default: Friday 10:00 AM ET) |
| `TEAMS_WEBHOOK_URL` | Failure alerts (always when set); success when `TEAMS_NOTIFY_ON_SUCCESS=true` |
| `TEAMS_NOTIFY_ON_SUCCESS` | Also post to Teams on successful / no-op runs (default: `false`) |
| `SMTP_*`, `ALERT_EMAIL_*` | Optional email failure alerts |

### Schedule examples

Weekly (default):

```env
SYNC_DAY_OF_WEEK=fri
SYNC_HOUR=10
SYNC_MINUTE=0
```

Daily during rollout:

```env
SYNC_DAY_OF_WEEK=mon,tue,wed,thu,fri
SYNC_HOUR=6
SYNC_MINUTE=0
```

## Testing without Microsoft credentials

You can exercise delta detection, validation, state management, and Everbridge SFTP while waiting for Entra credentials.

1. Put your test CSV at `Emergency_Alert_Registrations(in).csv` in the project root (or set `LOCAL_FALLBACK_CSV` to another path).
2. In `.env`, enable local mode and configure SFTP only:

   ```env
   SKIP_GRAPH_DOWNLOAD=true
   # Leave MS_* empty

   SFTP_HOST=sftp-aws-us3.everbridge.net
   SFTP_USERNAME=<your Everbridge account>
   SFTP_KEY_PATH=Apex.key
   ```

3. Initialize state if needed: `cp sync_state.json.example sync_state.json`
4. Preview delta without uploading:

   ```bash
   docker compose exec sftp-uploader uv run python admin.py preview
   ```

   For preview only, copy your sample to `master_download.csv` on the host first, or run a sync once (step 5) which copies the fallback file into `master_download.csv` inside the container.

5. Run a full end-to-end test (delta → SFTP → state commit):

   ```bash
   docker compose exec sftp-uploader uv run python main.py
   ```

6. Re-test the same row after editing the CSV:

   ```bash
   docker compose exec sftp-uploader uv run python admin.py reset-external-id <External ID>
   docker compose exec sftp-uploader uv run python main.py
   ```

**Production:** set `SKIP_GRAPH_DOWNLOAD=false` and `ALLOW_LOCAL_FALLBACK=false` once `MS_*` credentials are in place.

**Alternative:** `ALLOW_LOCAL_FALLBACK=true` (without `SKIP_GRAPH_DOWNLOAD`) tries OneDrive first and falls back to the local CSV only when Graph is unavailable. Use `SKIP_GRAPH_DOWNLOAD=true` when you want to skip Graph entirely.

## Operations

See **[docs/OPERATIONS.md](docs/OPERATIONS.md)** for the full command reference: starting/stopping the scheduler, one-off syncs, preview/status, contact deletion, and schedule configuration.

### Quick reference

```bash
# Start production scheduler
docker compose up -d --build

# Manual sync
docker compose exec sftp-uploader uv run python main.py

# Run once without starting the scheduler
docker compose exec sftp-uploader uv run python scheduler.py --run-now

# Admin
docker compose exec sftp-uploader uv run python admin.py status
docker compose exec sftp-uploader uv run python admin.py preview
docker compose exec sftp-uploader uv run python admin.py delete-external-id 1234
docker compose exec sftp-uploader uv run python admin.py reset-external-id 12345
docker compose exec sftp-uploader uv run python admin.py replay sent_files/upload_2026-01-01_10-00-00_abc.csv

# HTTP delete API (Power Automate) — default port 8080
docker compose up -d --build delete-api
curl -X DELETE http://localhost:8080/contacts/1234
curl -X POST http://localhost:8080/delete -H 'Content-Type: application/json' -d '{"employeeId":"1234"}'

# Laptop demo: public HTTPS URL for Power Automate (Cloudflare quick tunnel)
docker compose --profile tunnel up -d --build
docker compose logs -f tunnel   # copy https://….trycloudflare.com
# Then update the Power Automate HTTP action URI(s) to that new URL
# (quick-tunnel hostname changes on every start/restart)
```

`reset-external-id` only clears **local sync state** so a row can re-upload; it does not delete the contact from Everbridge.

`delete-external-id` (CLI) and the `delete-api` service both upload a delete CSV to Everbridge SFTP `/delete`, purge local state, archive under `sent_files/`, and send a Teams notification. Deleted contacts can be restored for 30 days per Everbridge policy. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for Power Automate wiring and the Cloudflare tunnel. Auth is not enabled yet — keep the API private, or only leave the tunnel up for short demos. After each tunnel start/restart, update Power Automate with the new `trycloudflare.com` URL from the logs.

## Troubleshooting

### Graph auth fails (403 / token error)

- Confirm Entra app has delegated `Files.Read.All` (and admin consent if required)
- Verify `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` in `.env`
- Re-run sign-in on the host: `uv run explore_onedrive.py --device-login`
- Ensure `ms_graph_token_cache.json` exists and is mounted in Docker

### Download fails (404)

- Verify `MS_FILE_ID` and `MS_FILE_PATH` in `.env`
- Run `explore_onedrive.py` to probe both ID and path download
- Sign in as the user who owns the master CSV (`/me/drive`)

### SFTP upload fails

- Confirm `Apex.key` is mounted and readable in the container
- Verify outbound port 22 is allowed from the host
- Check `failed_uploads/` for preserved staging files and metadata
- Failed uploads do **not** update `sync_state.json` — fix the issue and re-run

### Rows not uploading

- Run `admin.py preview` to see pending deltas
- Check if rows were rejected: `rejected_rows.csv`
- Confirm `External ID` is present and stable in the master CSV

### Secret rotation

| Secret | Action |
|--------|--------|
| Entra client secret | Create new secret in Entra portal → update `.env` → `docker compose up -d` |
| Graph token cache | Re-run `explore_onedrive.py --device-login` on host if refresh token revoked |
| Everbridge SFTP key | Replace `Apex.key` on host → restart container |
| Teams webhook | Regenerate webhook URL → update `TEAMS_WEBHOOK_URL` |

## Development

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
uv sync --all-groups
uv run pytest
```

Enable local CSV fallback for offline dev:

```env
ALLOW_LOCAL_FALLBACK=true
```

## Go-Live

See [ROADMAP.md](ROADMAP.md) for the full pre-launch checklist, [docs/VALIDATION_RUNBOOK.md](docs/VALIDATION_RUNBOOK.md) for test procedures, and [docs/BULK_LOAD_STRATEGY.md](docs/BULK_LOAD_STRATEGY.md) for org-wide rollout timing.

## Future: API Transport and HR Offboarding

Implemented today:

- **`admin.py delete-external-id`** — SFTP delete via `/delete`, confirmation prompt, local state purge, Teams notification
- **`api.py` / `delete-api` service** — HTTP `DELETE /contacts/{employeeId}` and `POST /delete` for Power Automate (no auth yet)

Still planned (not implemented yet):

- **Everbridge REST API** as an alternative or supplement to SFTP (`EVERBRIDGE_TRANSPORT=api`)
- **Auth on the delete HTTP API** and a fuller **HR offboarding web UI**
- **OneDrive Jobs folder** — archive sync/delete job CSVs and JSON metadata to `Projects/Emergency Alerts/Jobs/` (see [ROADMAP.md](ROADMAP.md) section 2.4)
- **Re-onboarding** — returning employees get a new External ID and use the registration Form again

Design details and remaining architectural work are in [docs/FUTURE_ARCHITECTURE.md](docs/FUTURE_ARCHITECTURE.md).
