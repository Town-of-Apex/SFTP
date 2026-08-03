# Pre-Go-Live Validation Runbook

Use this checklist before sending the emergency alert Form to all staff.

## Prerequisites

- [ ] Entra app registered with `Files.Read.All` (application permission) and admin consent granted
- [ ] `MS_*` and `SFTP_*` environment variables set in production `.env`
- [ ] `Apex.key` mounted in the Docker container
- [ ] `SKIP_GRAPH_DOWNLOAD=false` and `ALLOW_LOCAL_FALLBACK=false` in production
- [ ] At least one alert channel configured (`TEAMS_WEBHOOK_URL` and/or SMTP email)

## Offline testing (before Entra credentials)

Use this while waiting for Microsoft credentials to validate Everbridge SFTP and pipeline behavior.

- [ ] Place test CSV at `Emergency_Alert_Registrations(in).csv` (project root)
- [ ] Set `SKIP_GRAPH_DOWNLOAD=true` in `.env`; leave `MS_*` empty
- [ ] Configure `SFTP_*` and mount `Apex.key`
- [ ] `cp sync_state.json.example sync_state.json`
- [ ] **Preview**: `docker compose exec sftp-uploader uv run python admin.py preview`
- [ ] **Full sync**: `docker compose exec sftp-uploader uv run python main.py` → verify test contact in Everbridge
- [ ] **Re-upload test**: `admin.py reset-external-id <id>` → edit CSV → run sync again
- [ ] Before go-live: set `SKIP_GRAPH_DOWNLOAD=false` and complete Entra setup below

## CSV and Data Quality

- [ ] Download the master CSV manually from OneDrive and confirm headers match the Everbridge template (160+ columns, `END` terminator) plus analytics metadata after `END`: `Opted In`, `Submitter Email`, `Submitter Department`, `Submission Datetime`
- [ ] Confirm `External ID` is a stable employee identifier (not name-based)
- [ ] Confirm Power Automate appends **both** opt-ins and opt-outs to the master CSV, with `Opted In` set to `TRUE` or `FALSE`
- [ ] Confirm re-submissions append a new row with the same `External ID` (last-write-wins)

## Functional Tests

- [ ] **New person**: Submit a test Form entry → run `uv run python main.py` (or `scheduler.py --run-now`) → verify row appears in Everbridge
- [ ] **Update existing person**: Re-submit with changed phone/email → verify only the delta uploads and Everbridge upserts by `External ID`
- [ ] **Opt out**: Re-submit with `Opted In=FALSE` → verify DELETE upload and contact removed from Everbridge; confirm a later sync does **not** re-add them from the old opt-in row
- [ ] **Re-opt in**: After opt-out, submit again with `Opted In=TRUE` → verify UPDATE upload restores the contact
- [ ] **No changes**: Run sync again with no new Form submissions → verify "no action" and no duplicate upload
- [ ] **Invalid row**: Temporarily add a row missing `External ID` to master → verify it lands in `rejected_rows.csv` and valid rows still upload
- [ ] **SFTP failure**: Simulate failure (bad key or network block) → verify `failed_uploads/` preserved, alert sent, and **state not committed** → fix and re-run → same rows upload

## Operational Verification

- [ ] Run manual sync during business hours with IT on standby
- [ ] Verify archived file in `sent_files/` matches what Everbridge received
- [ ] Check Docker logs: `docker compose logs -f`
- [ ] Run `uv run python admin.py status` and `uv run python admin.py preview`

## Sign-off


| Role             | Name | Date | Notes |
| ---------------- | ---- | ---- | ----- |
| App owner        |      |      |       |
| IT / Entra       |      |      |       |
| Everbridge admin |      |      |       |


