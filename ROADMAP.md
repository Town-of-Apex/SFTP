# Everbridge Emergency Alert Sync — Go-Live Roadmap

Task list for taking the emergency alert registration pipeline from prototype to production. Check items off as you complete them.

**Legend:** P0 = blocker before go-live · P1 = high value soon after go-live · P2 = future backlog

---

## Current Architecture

```
MS Form → Power Automate → Master CSV (OneDrive)
                                ↓
                    This app (Docker, scheduled)
                                ↓
                    Everbridge SFTP (/update)
```

Everbridge **upserts by External ID**. Opt-outs are filtered upstream in Power Automate and never reach the master CSV.

---

## Phase 0 — Must Do Before Go-Live (P0)

### 0.1 Fix upload/state transaction order

- [x] Split delta detection into identify (read-only) and commit (write) steps
- [x] Commit `sync_state.json` only after successful SFTP upload
- [x] Preserve failed batches in `failed_uploads/` with metadata JSON
- [x] Leave staging CSV in place on failure for retry
- [x] **Verify:** Simulate SFTP failure → re-run → same rows upload successfully

### 0.2 Complete Microsoft Graph / OneDrive integration

- [x] Work with IT to register Entra app with `Files.Read.All` (application permission)
- [x] Obtain admin consent for application permissions
- [x] Run `uv run python explore_onedrive.py` to discover `MS_DRIVE_ID` and confirm file path
- [x] Copy `.env.example` to `.env` and populate all `MS_`* variables
- [x] Standardize filename to `Emergency_Alert_Registrations(in).csv` everywhere
- [x] Gate local CSV fallback behind `ALLOW_LOCAL_FALLBACK` (default `false`)
- [x] **Verify:** `docker compose exec sftp-uploader uv run python main.py` downloads from OneDrive with no local CSV present

### 0.3 Secrets and security hygiene

- [x] Add `.env`, `sync_state.json`, runtime CSVs, and archive dirs to `.gitignore`
- [x] Add `.dockerignore` to exclude secrets and runtime data from image build
- [x] Provide `sync_state.json.example` (empty array) for first-time setup
- [x] Remove `sync_state.json` from git tracking if previously committed (`git rm --cached sync_state.json`)
- [x] Store lean state entries (no full `row_data` PII)
- [x] Externalize SFTP settings to environment variables
- [x] Document secret rotation in README (Everbridge key, Entra client secret)
- [x] **Verify:** `git status` shows no PII or secret files

### 0.4 Operational alerting

- [x] Implement Teams webhook alerting on failure (`TEAMS_WEBHOOK_URL`)
- [x] Implement optional SMTP email alerting
- [x] Include failure type, sync run ID, row count, and failed file path in alerts
- [ ] Configure at least one Teams failure alert channel in production `.env`
- [ ] Configure at least one Teams success alert channel in production `.env`
- [ ] **Verify:** Simulated SFTP failure triggers a notification

### 0.5 Pre-go-live validation runbook

- [ ] Execute full checklist in [docs/VALIDATION_RUNBOOK.md](docs/VALIDATION_RUNBOOK.md)
- [ ] Obtain sign-off from app owner, IT, and Everbridge admin

### 0.6 Initial bulk-load strategy

- [ ] Review and adopt plan in [docs/BULK_LOAD_STRATEGY.md](docs/BULK_LOAD_STRATEGY.md)
- [ ] Plan daily sync (or `--run-now`) during rollout window
- [ ] Return to weekly Friday schedule after rollout plateaus

---

## Phase 1 — Should Have (P1, Soon After Go-Live)

### 1.1 Refactor for maintainability

- [x] Split into `src/` modules: `config`, `graph_client`, `delta`, `sftp_client`, `pipeline`, `notifications`, `validation`
- [x] Thin `main.py` and `scheduler.py` entry points
- [x] Add `admin.py` CLI

### 1.2 Structured logging

- [x] Replace `print()` with `logging` throughout pipeline
- [x] Add `sync_run_id` (UUID) per execution for log correlation

### 1.3 Retries and resilience

- [x] Graph auth and download retries with exponential backoff
- [x] SFTP upload retries with remote file size verification
- [x] Scheduler `misfire_grace_time` for missed runs

### 1.4 Configurable schedule

- [x] Schedule via `SYNC_DAY_OF_WEEK`, `SYNC_HOUR`, `SYNC_MINUTE`, `SYNC_TIMEZONE`
- [x] Support comma-separated days (e.g. `mon,tue,wed,thu,fri`) for rollout
- [x] `--run-now` flag on scheduler

### 1.5 CSV validation layer

- [x] Validate required fields: `External ID`, `First Name`, `Last Name`, contact method
- [x] Basic phone and email format checks
- [x] Write invalid rows to `rejected_rows.csv` without blocking valid rows

### 1.6 Unit tests and CI

- [x] Pytest suite for config, delta, validation, and pipeline
- [x] Run `uv sync --all-groups && uv run pytest` locally before each release

### 1.7 External ID–aware delta tracking

- [x] Track `external_id` and `is_update` in state entries
- [x] Log new vs. update counts during identification

### 1.8 Documentation

- [x] Rewrite README with end-to-end architecture and troubleshooting
- [x] Validation runbook and bulk-load strategy docs

### 1.9 Explorer tool in Docker image

- [x] Include `explore_onedrive.py` in Dockerfile

---

## Phase 2 — Nice to Have (P2, Future Backlog)

### 2.1 SharePoint migration

- [ ] Document resolving drive ID from SharePoint site URL via Graph `/sites/{hostname}:/{path}:/drive`
- [ ] Test with SharePoint document library when migrating off personal OneDrive

### 2.2 Field mapping configuration

- [ ] Add `field_mapping.yaml` for Form column → Everbridge column transforms
- [ ] Apply mapping in pipeline without code changes when Form fields evolve

### 2.3 Admin CLI enhancements

- [x] `admin.py status` — state summary and pending row count
- [x] `admin.py preview` — dry-run delta preview
- [x] `admin.py replay` — re-upload archived CSV
- [x] `admin.py reset-external-id` — force re-send for one person
- [ ] `admin.py` migrate legacy state entries that contain `row_data`

### 2.8 Audit log

- [ ] Append-only JSONL: source file hash, rows sent, filename, operator, duration

---

## Upstream Dependencies (Cross-Team)


| Owner          | Task                                                                     | Status |
| -------------- | ------------------------------------------------------------------------ | ------ |
| Power Automate | Master CSV is Everbridge-format + metadata after `END`; append opt-ins and opt-outs | [ ]    |
| Power Automate | `External ID` = stable employee ID; re-submissions append new rows; `Opted In` = TRUE/FALSE | [ ]    |
| IT / Entra     | App registration, `Files.Read.All`, admin consent, secret lifecycle      | [ ]    |
| Everbridge     | SFTP key active; `/update` and `/delete` paths verified                  | [ ]    |
| Comms / HR     | Form wording, opt-out affirmation, rollout timing                        | [ ]    |
| Host / Ops     | Docker host, outbound port 22, persistent volumes for state and archives | [ ]    |


---

## Recommended Go-Live Sequence

1. Fix and verify state-before-upload behavior (0.1)
2. Wire Graph API with IT (0.2)
3. Configure alerting and security (0.3, 0.4)
4. Pilot with 10–20 staff; sync daily
5. Execute validation runbook (0.5)
6. Org-wide Form send with bulk-load plan (0.6)
7. Return to weekly schedule; implement remaining P1/P2 items incrementally

---

## Quick Reference Commands

```bash
# Start production scheduler
docker compose up -d --build

# Manual sync
docker compose exec sftp-uploader uv run python main.py

# Run once without starting scheduler
docker compose exec sftp-uploader uv run python scheduler.py --run-now

# Preview pending deltas
docker compose exec sftp-uploader uv run python admin.py preview

# Check state
docker compose exec sftp-uploader uv run python admin.py status

# Graph / OneDrive diagnostics
docker compose exec sftp-uploader uv run python explore_onedrive.py

# Run tests locally
uv sync --all-groups && uv run pytest
```

