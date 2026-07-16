# Health Check & Monitoring Plan

Implementation plan for startup readiness, periodic health checks, and Teams notifications. **Do not implement yet** — this is a design backlog to pick up later.

Related existing pieces:

- Teams / Power Automate adaptive-card webhook: `src/notifications.py` (`TEAMS_WEBHOOK_URL`)
- Scheduler: `scheduler.py` (APScheduler, no HTTP, no next-run reporting)
- Delete API liveness only: `GET /health` in `api.py` (Compose healthcheck on `delete-api` only)
- Ops commands: `docs/OPERATIONS.md`

---

## Goals

1. **Startup ready notification** — When the scheduler container starts and passes readiness checks, post a Teams card that includes:
   - Overall ready / degraded status
   - Next scheduled Everbridge export time(s)
   - Which dependency checks passed or failed
2. **Periodic health checks** — On a configurable cadence (default: daily), re-check that the container is still fit to run the next export, and notify Teams (configurable).
3. **Dead-man / “everything is down” coverage** — If the host or container(s) cannot run code at all, an **external** Power Automate flow must detect silence and alert Teams.
4. **Feature toggles** — Env vars to enable/disable notifications and control check cadence without code changes.

## Non-goals (for this plan)

- Full metrics / Prometheus / APM
- Replacing existing sync success/failure alerts (`TEAMS_NOTIFY_ON_SUCCESS`, failure cards)
- Authenticating the delete API (separate security item)
- Guaranteeing Everbridge will accept the next batch (health ≠ dry-run sync)

---

## Current gaps

| Area | Today |
| --- | --- |
| Scheduler container | No Compose `healthcheck`; process can die quietly under `restart: unless-stopped` |
| Next scheduled run | Logged as cron expression only; not computed or sent to Teams |
| Run / heartbeat metadata | `sync_state.json` updates only on successful uploads — not a heartbeat |
| Dependency probes | No Graph / SFTP / writable-paths smoke checks outside a full sync |
| External dead-man | Nothing watches the scheduler from outside the container |
| Notification toggles | Only `TEAMS_NOTIFY_ON_SUCCESS` for sync results; no health-specific flags |

---

## Recommended architecture (hybrid)

Split responsibilities by failure mode:

```text
┌─────────────────────────────────────────────────────────────┐
│  Inside Docker (sftp-uploader)                              │
│  • Startup readiness + Teams “Scheduler Ready” card         │
│  • APScheduler interval job for periodic health             │
│  • Persist health_state.json + optional HTTP /status        │
│  • Docker HEALTHCHECK on heartbeat freshness                │
└───────────────────────────┬─────────────────────────────────┘
                            │ posts when alive
                            ▼
                   Teams (via existing webhook)
                            ▲
                            │ posts when unreachable / stale
┌───────────────────────────┴─────────────────────────────────┐
│  Outside Docker (Power Automate)                            │
│  • Scheduled flow (e.g. every 15–60 min or daily)           │
│  • Probe HTTP health OR OneDrive heartbeat file age         │
│  • Alert Teams only on failure / recovery                   │
└─────────────────────────────────────────────────────────────┘
```

**Why not a separate “monitor” container as the primary design?**

A sidecar that only pings localhost and posts to Teams still dies with the host. It can help for “scheduler process crashed but Docker is up,” but Docker’s built-in `healthcheck` + `restart` already covers much of that. Prefer:

1. **In-process checks** for “am I ready?” (rich context, next run time, Graph/SFTP probes).
2. **Power Automate** for “is anything listening?” (true external observer).
3. Optional later: a tiny watchdog service only if PA cannot reach the host and OneDrive heartbeat is undesirable.

### Probe surface options (pick one for Power Automate)

| Option | Pros | Cons |
| --- | --- | --- |
| **A. HTTP `/status` on scheduler** (recommended if host can expose a port or private URL) | Simple for PA HTTP action; rich JSON | Need to bind a port on `sftp-uploader`; network path from PA → host |
| **B. OneDrive heartbeat file** via Graph (e.g. `health_heartbeat.json`) | No inbound ports; fits existing Graph auth; PA can read OneDrive | Extra Graph write; token issues look like “down” |
| **C. Sidecar monitor container** | Local Docker visibility | Still host-dependent; more Compose surface |

**Recommendation:** Implement **A** for local/Docker health + PA when reachable; add **B** as the preferred dead-man if the scheduler host is not HTTP-reachable from Power Automate (common for on-prem Docker hosts).

---

## Check levels

### Liveness (process up)

- Scheduler process running
- APScheduler started and has ≥1 job registered
- Heartbeat timestamp updated within `HEALTH_HEARTBEAT_MAX_AGE_SECONDS`

### Readiness (good to run the next export)

Non-mutating probes:

1. **Config loaded** — required env present (Graph, SFTP, schedule)
2. **Timezone / schedule parseable** — same validation as scheduler startup
3. **Writable paths** — `sync_state.json` parent, `sent_files/`, `failed_uploads/`, token cache path
4. **Graph / token** — MSAL cache loadable; lightweight Graph call (e.g. get file metadata for `MS_FILE_ID` / path) — no full CSV download
5. **SFTP** — connect + `listdir` remote update dir (or equivalent non-write check); disconnect — no upload
6. **Next run computable** — from APScheduler `job.next_run_time` (preferred) or `CronTrigger.get_next_fire_time()`

Do **not** require Teams webhook success for readiness (Teams down should not block “ready to sync”). Optionally report Teams as a soft check.

### Periodic health (daily by default)

Same readiness suite, plus:

- Heartbeat age OK
- Optional: “last sync run” summary from `health_state.json` (status, time, run id) — informational, not a hard fail if never run yet
- Optional soft warning if last sync failed and not yet recovered (sync failures already alert separately)

---

## Durable state: `health_state.json`

Do **not** overload `sync_state.json`. Add a small JSON file (bind-mount like other runtime files):

```json
{
  "schema_version": 1,
  "scheduler_started_at": "2026-07-16T15:40:00-04:00",
  "last_heartbeat_at": "2026-07-16T15:40:05-04:00",
  "last_health_check_at": "2026-07-16T15:40:05-04:00",
  "last_health_status": "ready",
  "last_health_details": {
    "graph": "ok",
    "sftp": "ok",
    "paths": "ok",
    "schedule": "ok"
  },
  "next_scheduled_runs": [
    {"job_id": "everbridge_upload_0_fri", "next_run_at": "2026-07-17T10:00:00-04:00"}
  ],
  "last_sync_run_id": null,
  "last_sync_status": null,
  "last_sync_finished_at": null
}
```

Update:

- On scheduler start (after jobs registered)
- After each health check
- After each `run_sync()` completion (hook from `scheduler.job()` / pipeline)

Mount in Compose: `./health_state.json:/app/health_state.json` (and add `health_state.json` to `.gitignore` / `.dockerignore`).

---

## Teams notifications

Reuse `_build_teams_adaptive_card_payload` / `_send_teams_webhook` in `src/notifications.py`. Add dedicated helpers (names illustrative):

| Helper | When | Gated by |
| --- | --- | --- |
| `send_startup_health_alert` | Container ready (or degraded) after startup checks | `HEALTH_NOTIFY_ON_STARTUP` |
| `send_periodic_health_alert` | After interval health job | `HEALTH_NOTIFY_PERIODIC` (+ optional `HEALTH_NOTIFY_PERIODIC_ONLY_ON_CHANGE`) |
| `send_health_degraded_alert` | Transition ready → not ready | `HEALTH_NOTIFY_ON_DEGRADED` |
| `send_health_recovered_alert` | Transition not ready → ready | `HEALTH_NOTIFY_ON_RECOVERY` |

Suggested card titles / fields:

**Startup (ready)**

```text
Everbridge Scheduler Ready
Status: ready
container: sftp-everbridge-scheduler
started_at: ...
next_run: Fri 2026-07-17 10:00 America/New_York
checks: graph=ok, sftp=ok, paths=ok, schedule=ok
```

**Startup (degraded)** — still start the scheduler (so cron keeps trying), but make the card loud:

```text
Everbridge Scheduler Degraded
Status: degraded
...
failed_checks: sftp=connection timed out
next_run: ...
```

**Periodic OK** (if enabled; default off or “only on change” to reduce noise):

```text
Everbridge Scheduler Health Check
Status: ready
checked_at: ...
next_run: ...
```

**Power Automate dead-man** (separate flow; may use a different Teams post action):

```text
Everbridge Scheduler Unreachable
Status: down
probe: HTTP /status (or OneDrive heartbeat)
detail: timeout / stale heartbeat > N minutes
```

Keep health cards independent of `TEAMS_NOTIFY_ON_SUCCESS` so ops can silence sync noise without silencing health (or vice versa).

---

## Configuration (env vars)

Add to `src/config.py` and `.env.example`:

```env
# --- Health / monitoring ---
# Master switches
HEALTH_CHECKS_ENABLED=true
HEALTH_NOTIFY_ON_STARTUP=true
HEALTH_NOTIFY_PERIODIC=false
HEALTH_NOTIFY_PERIODIC_ONLY_ON_CHANGE=true
HEALTH_NOTIFY_ON_DEGRADED=true
HEALTH_NOTIFY_ON_RECOVERY=true

# Cadence (APScheduler interval for in-container checks)
HEALTH_CHECK_INTERVAL_HOURS=24
# Optional: run first periodic check N minutes after startup (0 = with startup only)
HEALTH_CHECK_STARTUP_DELAY_MINUTES=0

# Heartbeat / Docker HEALTHCHECK
HEALTH_STATE_PATH=health_state.json
HEALTH_HEARTBEAT_MAX_AGE_SECONDS=900

# What to probe (soft toggles — disable expensive/flaky checks)
HEALTH_PROBE_GRAPH=true
HEALTH_PROBE_SFTP=true
HEALTH_PROBE_PATHS=true

# Optional HTTP status server inside scheduler (for Docker + Power Automate)
HEALTH_HTTP_ENABLED=false
HEALTH_HTTP_HOST=0.0.0.0
HEALTH_HTTP_PORT=8081

# Optional OneDrive heartbeat for external dead-man (if HTTP not reachable)
HEALTH_ONEDRIVE_HEARTBEAT_ENABLED=false
HEALTH_ONEDRIVE_HEARTBEAT_PATH=Projects/Emergency Alerts/scheduler_heartbeat.json
```

Notes:

- If `HEALTH_CHECKS_ENABLED=false`, skip probes, HTTP server, and health Teams cards; scheduler still runs sync jobs.
- Periodic Teams spam: prefer `HEALTH_NOTIFY_PERIODIC=false` with `HEALTH_NOTIFY_ON_DEGRADED` / `HEALTH_NOTIFY_ON_RECOVERY=true`, plus PA dead-man for total outage.
- Reuse `TEAMS_WEBHOOK_URL`; no second webhook required unless you want a dedicated ops channel later (`HEALTH_TEAMS_WEBHOOK_URL` optional override).

---

## Implementation outline (in-app)

### Phase 1 — Foundation (no PA yet)

1. **`src/health.py`**
   - `run_health_checks(config, scheduler=None) -> HealthReport`
   - Probes: paths, schedule/next runs, Graph metadata, SFTP connect
   - `write_health_state` / `read_health_state`
   - `update_heartbeat()`
2. **`src/notifications.py`**
   - Health card builders + send helpers gated by new config flags
3. **`src/config.py` + `.env.example`**
   - New fields and `_env_bool` / int parsing
4. **`scheduler.py`**
   - After jobs are added: compute next runs, run startup checks, write state, send startup card
   - Register interval job for periodic health
   - On each sync job completion: update last sync fields in health state
5. **Compose**
   - Mount `health_state.json`
   - Add `healthcheck` for `sftp-uploader` that fails if heartbeat is stale, e.g. a small `python -c` that reads `HEALTH_STATE_PATH` and exits non-zero if `last_heartbeat_at` older than max age
6. **`admin.py status`**
   - Print next run(s), last health status, heartbeat age
7. **Tests**
   - Unit tests for probes (mocked Graph/SFTP), state I/O, notification gating, next-run formatting

### Phase 2 — HTTP status (enables simple PA + richer Docker check)

1. Lightweight threaded HTTP server or embed a minimal FastAPI/Starlette app in the scheduler process:
   - `GET /health` → liveness (`{"status":"ok"}` if heartbeat fresh)
   - `GET /ready` → 200 if ready, 503 if degraded
   - `GET /status` → full JSON from `health_state.json` + live next runs
2. Publish port in Compose when `HEALTH_HTTP_ENABLED=true` (e.g. `8081:8081`)
3. Point Compose healthcheck at `http://127.0.0.1:8081/health`
4. Document firewall / reverse-proxy expectations in `docs/OPERATIONS.md`

**Threading note:** `BlockingScheduler` owns the main thread — run the HTTP server in a daemon thread (same pattern many APScheduler apps use), or switch to `BackgroundScheduler` + block on `Event.wait()` (slightly larger refactor).

### Phase 3 — Power Automate dead-man

Choose probe mode:

#### 3a. HTTP probe (if reachable)

Flow (recurrence):

1. HTTP GET `https://<host-or-tunnel>/status` (or `/health`)
2. On non-2xx / timeout → post Teams “Unreachable”
3. Optional: on return to 2xx after failure → “Recovered”
4. Store last result in flow variables or a SharePoint list to avoid duplicate alerts

#### 3b. OneDrive heartbeat (if no inbound HTTP)

App side (Phase 1/2 addition):

- On each successful health check / heartbeat, write a small JSON file to OneDrive via Graph (timestamp + status + next_run)

Flow:

1. Recurrence → Get file content / metadata from OneDrive
2. If missing or `last_heartbeat_at` older than threshold → Teams alert
3. Else no-op (or daily digest if desired)

Document the flow steps in `docs/OPERATIONS.md` (no flow definition is stored in-repo today for Form ingestion either).

### Phase 4 — Polish (optional)

- `HEALTH_NOTIFY_PERIODIC_ONLY_ON_CHANGE` to suppress identical daily OK cards
- Soft vs hard failure classification (e.g. Graph flaky → warn, SFTP down → degraded)
- Include delete-api in the same PA flow (`GET :8080/health`) as a second check
- Roadmap checkbox under Phase 1 / ops alerting

---

## Docker specifics

### `sftp-uploader` today

- No published ports, no healthcheck
- Default CMD: `uv run python scheduler.py`

### Proposed Compose additions

```yaml
# illustrative — finalize during implementation
sftp-uploader:
  volumes:
    - ./health_state.json:/app/health_state.json
  # when HEALTH_HTTP_ENABLED=true:
  ports:
    - "${HEALTH_HTTP_PORT:-8081}:8081"
  healthcheck:
    test: ["CMD", "uv", "run", "python", "-m", "src.health_check_cli", "--liveness"]
    interval: 60s
    timeout: 10s
    retries: 3
    start_period: 60s
```

`start_period` should allow Graph/SFTP probe time on cold start.

A **separate monitor container is not required** for v1. Revisit only if:

- You want health probes isolated from the scheduler process, or
- You cannot run HTTP inside the scheduler and cannot use OneDrive heartbeat

---

## Power Automate vs in-app — responsibility matrix

| Event | Who detects | Who notifies |
| --- | --- | --- |
| Container starts, deps OK | Scheduler | Scheduler → Teams |
| Container starts, SFTP/Graph broken | Scheduler | Scheduler → Teams (degraded) |
| Daily “still OK” | Scheduler (optional) | Scheduler → Teams (optional) |
| Deps flip to bad while running | Scheduler interval job | Scheduler → Teams (degraded) |
| Scheduler process crash; Docker restarts it | Docker restart + startup card | Startup card on recovery |
| Host down / Docker daemon down / network isolated | **Power Automate** | **PA → Teams** |
| Teams webhook itself down | Logs only | Cannot notify; PA email fallback optional |

---

## Suggested implementation order when you pick this up

1. Env + config + `health_state.json` + path/schedule checks (no external I/O)
2. Wire startup card with **next run time** from APScheduler (high user value, low risk)
3. Add Graph + SFTP probes behind flags
4. Periodic interval job + degraded/recovery notifications
5. Compose healthcheck on heartbeat file
6. Optional HTTP `/status`
7. Power Automate dead-man flow (HTTP or OneDrive)
8. Docs: `.env.example`, `docs/OPERATIONS.md`, optional ROADMAP checkbox

---

## Verification checklist (for later)

- [ ] Fresh `docker compose up` posts one Startup Ready card with correct next Friday (or configured) run time
- [ ] Break SFTP host in `.env` → Startup Degraded card; scheduler still running
- [ ] `HEALTH_NOTIFY_ON_STARTUP=false` → no startup Teams message
- [ ] `HEALTH_CHECKS_ENABLED=false` → no probes / health cards; sync schedule unchanged
- [ ] Stop container; PA flow alerts within one recurrence interval
- [ ] Start container again; PA recovers (or startup card fires); no alert storm
- [ ] `admin.py status` shows next run + last health
- [ ] Unit tests cover gating flags and next-run formatting
- [ ] Heartbeat Compose healthcheck flips unhealthy if process stops updating state

---

## Open decisions (resolve at implementation time)

1. **PA probe: HTTP vs OneDrive heartbeat?** Depends on whether the Docker host is reachable from Power Automate.
2. **Default `HEALTH_NOTIFY_PERIODIC`?** Recommend `false`; rely on degraded/recovery + PA dead-man to avoid daily noise.
3. **Should degraded startup block the scheduler from starting jobs?** Recommend **no** — still schedule; alert loudly. (Alternate: exit non-zero so Compose marks unhealthy — more aggressive.)
4. **Shared vs separate Teams channel / webhook for health?** Start with existing `TEAMS_WEBHOOK_URL`; add override only if noise is a problem.
5. **Include `delete-api` in the same monitoring story?** Nice follow-on; it already has `/health`.

---

## File touch list (expected)

| File | Change |
| --- | --- |
| `HEALTH_CHECK_PLAN.md` | This plan |
| `src/health.py` | New probes + state |
| `src/notifications.py` | Health cards |
| `src/config.py` | New settings |
| `scheduler.py` | Startup, interval job, next-run |
| `docker-compose.yml` | Mount, healthcheck, optional port |
| `.env.example` | Document flags |
| `admin.py` | Richer status |
| `docs/OPERATIONS.md` | Ops runbook for health + PA flow |
| `tests/test_health.py` | New |
| `tests/test_notifications.py` | Extend |
| `.gitignore` | `health_state.json` |

---

## Summary recommendation

Build health **inside the existing `sftp-uploader` scheduler** (startup + daily/interval checks + Teams via the current webhook), persist `health_state.json`, and use **Power Automate as the external dead-man** for host/container-down. Add an optional status HTTP port or OneDrive heartbeat so PA has something to probe. Keep everything behind env toggles so notifications can be silenced or quieted without redeploying code.
)
