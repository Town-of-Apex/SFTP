# Operations Guide

Day-to-day commands for running the Everbridge emergency alert sync in Docker.

All commands assume you are in the project root and the stack is built (`docker compose up -d --build`).

---

## Start and stop the app

### Start the scheduled sync (production)

Starts the container and runs the cron-style scheduler (default: Friday 10:00 AM Eastern).

```bash
docker compose up -d --build
```

### View logs

```bash
docker compose logs -f
```

### Stop the scheduler

```bash
docker compose down
```

The container stops; bind-mounted data (`sync_state.json`, `sent_files/`, `failed_uploads/`, token cache) remains on the host.

---

## One-off sync jobs

### Run a full sync immediately (via main entry point)

Downloads the master CSV from OneDrive, detects new/updated rows, validates, uploads to Everbridge SFTP `/update`, and commits state on success.

```bash
docker compose exec sftp-uploader uv run python main.py
```

### Run once via the scheduler (same pipeline)

Useful when the scheduler container is already running:

```bash
docker compose exec sftp-uploader uv run python scheduler.py --run-now
```

### Run sync via admin CLI

Same as `main.py`; prints a short result summary:

```bash
docker compose exec sftp-uploader uv run python admin.py run
```

---

## Inspect sync state (no upload)

### Pending delta preview

Shows how many rows would upload on the next sync and lists contact names (up to 20):

```bash
docker compose exec sftp-uploader uv run python admin.py preview
```

Requires `master_download.csv` in the container (run a sync once, or copy a sample CSV to that path on the host).

### State summary

```bash
docker compose exec sftp-uploader uv run python admin.py status
```

---

## Delete a contact from Everbridge

Removes a contact by **External ID** using Everbridge's SFTP `/delete` folder. Also purges local sync state so the row will not re-upload unless it reappears in the master CSV.

### Interactive delete (recommended)

Prompts you to re-type the External ID before uploading:

```bash
docker compose exec sftp-uploader uv run python admin.py delete-external-id 1234
```

### Skip confirmation (automation / scripts)

```bash
docker compose exec sftp-uploader uv run python admin.py delete-external-id 1234 --yes
```

### Dry run (build delete CSV only)

Writes `everbridge_delete.csv` locally without SFTP upload:

```bash
docker compose exec sftp-uploader uv run python admin.py delete-external-id 1234 --dry-run
```

### After delete

- A copy of the delete CSV is archived under `sent_files/delete_{timestamp}_{id}.csv`.
- A Teams notification is sent (if `TEAMS_WEBHOOK_URL` is set) with the contact name and a note that deleted contacts can be restored for 30 days.
- **This is not the same as** `reset-external-id`, which only clears local state and does **not** remove the contact from Everbridge.

---

## Delete via HTTP (Power Automate / Teams)

The `delete-api` Compose service exposes a minimal HTTP API (no auth yet — keep the host/port private). Same Everbridge delete path as the CLI.

```bash
docker compose up -d --build delete-api
```

Listens on host port `8080` by default (`DELETE_API_PORT` overrides the published port).

### Endpoints

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | — | Liveness |
| `DELETE` | `/contacts/{employeeId}` | — | Delete by External ID |
| `POST` | `/delete` | `{"employeeId": "1234"}` | Same delete (Power Automate-friendly) |

### Power Automate HTTP action examples

After the adaptive-card confirmation, call either:

**DELETE**

- Method: `DELETE`
- URI: `http://<host>:8080/contacts/<employeeId>`

**POST** (often easier when the ID comes from a card input)

- Method: `POST`
- URI: `http://<host>:8080/delete`
- Headers: `Content-Type: application/json`
- Body:

```json
{
  "employeeId": "1234"
}
```

Success response (`200`):

```json
{
  "status": "deleted",
  "employeeId": "1234",
  "contact": "Jane Doe",
  "archive": "sent_files/delete_2026-07-13_10-00-00_1234.csv",
  "stateEntriesRemoved": 1
}
```

Errors: `400` bad/missing ID, `502` Everbridge/SFTP failure, `503` master CSV unavailable.

### Expose to Power Automate (Cloudflare quick tunnel)

Power Automate runs in Microsoft's cloud, so it **cannot** call `localhost` on your laptop. For a laptop demo, use a free Cloudflare **quick tunnel** (no Cloudflare account required).

```bash
# Start delete API + public tunnel
docker compose --profile tunnel up -d --build

# Read the public HTTPS URL (look for https://….trycloudflare.com)
docker compose logs -f tunnel
```

**Reminder:** every time you start or restart the tunnel container, Cloudflare issues a **new** `https://….trycloudflare.com` URL. Update the HTTP action URI(s) in your Power Automate flow to match before testing again — an old hostname will fail.

Use that base URL in Power Automate, for example:

- `GET  https://….trycloudflare.com/health`
- `POST https://….trycloudflare.com/delete` with `{"employeeId":"1234"}`

Notes:

- The `trycloudflare.com` hostname **changes every time** the tunnel container restarts (or `docker compose --profile tunnel up` recreates it). Always copy the new URL from `docker compose logs tunnel` and paste it into Power Automate.
- This publishes an **unauthenticated** delete endpoint on the public internet — use only for short demos, then stop it:

```bash
docker compose --profile tunnel down
```

For a lasting hostname later, create a named Cloudflare Tunnel (account + token) instead of the quick tunnel — then you will not need to update Power Automate on every restart.

---

## Other admin commands

### Force re-upload on next sync (local state only)

Clears sync state for one External ID so the next sync treats the master CSV row as new:

```bash
docker compose exec sftp-uploader uv run python admin.py reset-external-id 1234
```

### Re-upload a previous archived CSV

```bash
docker compose exec sftp-uploader uv run python admin.py replay sent_files/upload_2026-01-01_10-00-00_abc12345.csv
```

---

## OneDrive / Graph diagnostics

Re-authenticate or probe file paths (run on host or in container):

```bash
uv run python explore_onedrive.py --device-login
docker compose exec sftp-uploader uv run python explore_onedrive.py
```

---

## Scheduled job configuration

Set in `.env`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `SYNC_TIMEZONE` | `America/New_York` | Cron timezone |
| `SYNC_DAY_OF_WEEK` | `fri` | Day(s); comma-separated for multiple (e.g. `mon,tue,wed,thu,fri`) |
| `SYNC_HOUR` | `10` | Hour (24h) |
| `SYNC_MINUTE` | `0` | Minute |

Restart after changing schedule:

```bash
docker compose up -d
```

---

## Alerting

| Variable | Purpose |
|----------|---------|
| `TEAMS_WEBHOOK_URL` | Teams channel for failures (and deletes) |
| `TEAMS_NOTIFY_ON_SUCCESS` | Set `true` to also notify on successful syncs and no-op runs |

Success and failure sync alerts include contact names when rows were processed. Delete alerts always post to Teams when the webhook is configured.

---

## Artifact locations (local)

| Path | Contents |
|------|----------|
| `sent_files/` | Successful upload CSVs and delete CSV archives |
| `failed_uploads/` | Failed SFTP batches (CSV + JSON metadata) |
| `rejected_rows.csv` | Rows that failed validation on the last run |
| `sync_state.json` | Committed row signatures (no PII) |

**Planned:** mirror job artifacts to OneDrive `Projects/Emergency Alerts/Jobs/` — see [ROADMAP.md](../ROADMAP.md) section 2.4.

---

## Tests (local dev)

```bash
uv sync --all-groups && uv run pytest
```
