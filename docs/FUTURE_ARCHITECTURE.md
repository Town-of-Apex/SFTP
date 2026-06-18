# Future Architecture — API, Deletions, and HR Offboarding

This document captures planned capabilities beyond the current **CSV → SFTP upsert** pipeline. Nothing here is implemented yet; it guides future work and explains preparatory choices in the codebase today.

## Vision

Today the system is **additive only**: new Form submissions and updates flow to Everbridge. There is no path to remove a contact when someone leaves the organization.

The long-term goal is a small set of HR-facing tools:

1. **Registration (exists today)** — staff opt in via Microsoft Form; deltas sync to Everbridge on a schedule.
2. **Offboarding (future)** — HR opens a simple form or internal web app, enters an employee's **External ID**, confirms, and the system removes that contact from Everbridge if it exists.
3. **Re-onboarding (future)** — if the person returns, they receive a **new External ID** and complete the registration Form again like any new hire.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  MS Form        │     │  This application │     │  Everbridge         │
│  (opt-in/update)│────▶│  scheduled sync   │────▶│  contacts (upsert)  │
└─────────────────┘     └──────────────────┘     └─────────────────────┘

┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  HR webapp /    │     │  delete service   │     │  Everbridge         │
│  Form (future)  │────▶│  (on-demand)      │────▶│  contact removed    │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
```

---

## Planned Capability: Everbridge API Transport

### Why

SFTP batch CSV upload works well for scheduled bulk upserts but is awkward for:

- Immediate, single-contact **deletes**
- Real-time feedback ("contact not found", "delete succeeded")
- Finer error handling per operation

Everbridge exposes REST APIs for contact management. A future **`api`** transport (alongside or instead of **`sftp`**) would call those endpoints directly.

### Configuration (planned)

```env
# Transport selection (implemented as env hook; only sftp is active today)
EVERBRIDGE_TRANSPORT=sftp          # future: api | sftp

# Future API credentials (not used until api transport is built)
EVERBRIDGE_API_BASE_URL=
EVERBRIDGE_API_KEY=
EVERBRIDGE_ORG_ID=
```

### Design approach

- All Everbridge write operations go through a **transport interface** (`src/everbridge/protocol.py`).
- `SftpTransport` implements batch upsert via staged CSV (current behavior).
- `ApiTransport` (future) implements `upsert_contacts` and `delete_contact` via REST.
- The scheduled pipeline calls `transport.upsert_batch(...)` without knowing SFTP vs API details.
- A separate **on-demand delete path** (CLI, admin command, or small web service) calls `transport.delete_contact(external_id)`.

SFTP and API can coexist during migration: e.g. scheduled sync stays on SFTP while deletes use API, then cut over upserts when API is proven.

---

## Planned Capability: HR Offboarding / Contact Deletion

### User story

> As HR, when an employee leaves, I open an internal page, enter their employee ID (External ID), see a confirmation screen with their name if found, click Confirm, and the contact is removed from Everbridge. I receive a success or "not found" message. If they return later, they re-register under a new ID.

### Proposed flow

1. HR submits External ID via authenticated internal UI (Entra SSO recommended).
2. Backend validates ID format and optional HR role permission.
3. Optional: lookup contact in Everbridge by External ID and show name for confirmation.
4. On confirm:
   - Call `delete_contact(external_id)` on the active transport (API preferred; SFTP delete file format TBD with Everbridge docs).
   - **Purge that External ID from local `sync_state.json`** so a stale hash never blocks a future re-registration under a new ID for a different person.
   - Append to a **deletion audit log** (who, when, external_id, result).
5. Notify HR (and optionally IT) on success or failure.

### Re-onboarding

- Returning employees are treated as **new registrants** with a **new External ID** assigned by HR/identity systems.
- The registration Form and scheduled sync handle them like any other new row.
- No special "un-delete" path — deletion is intentional and permanent from Everbridge's perspective.

### Open questions to resolve before build

- [ ] Confirm Everbridge API endpoint and auth model for contact delete in your org's tenant.
- [x] SFTP delete via `/delete` folder with full template CSV (External ID only required) — implemented as `admin.py delete-external-id`
- [ ] Who may trigger deletes (HR only? delegated admins?) — drives Entra app roles for future web UI.
- [ ] Should deletion also remove/archive the employee's rows in the Power Automate master CSV, or only Everbridge? (Master CSV is append-only today.)

---

## Architectural Preparations Already in Place

These choices were made so future work slots in without a rewrite:

| Area | Current state | Why it helps later |
|------|---------------|-------------------|
| **Transport abstraction** | `src/everbridge/` with `ContactTransport` protocol and `SftpTransport` | API client is a new class, not a pipeline fork |
| **`EVERBRIDGE_TRANSPORT` env** | Defaults to `sftp`; `api` reserved | Switch transports without code changes |
| **External ID in sync state** | State entries store `external_id`, not full PII | Delete flow can purge by ID; re-registration is clean |
| **`admin.py reset-external-id`** | Removes state for one ID | Precursor to delete + state purge; same helper reused |
| **Modular `src/` layout** | graph, delta, validation, notifications separate | Delete service is a new module or thin FastAPI app, not a monolith |
| **Audit log (ROADMAP 2.8)** | Planned JSONL for sync runs | Same pattern extends to deletion audit |

### Recommended additions when delete work starts

1. **`deletions.jsonl`** (or shared audit log) — append-only: timestamp, operator, external_id, transport, result.
2. **`admin.py delete-external-id <id>`** — CLI for IT until HR webapp exists; calls transport + state purge. **Implemented** (SFTP `/delete`, confirmation prompt, Teams alert).
3. **Small web service** — optional FastAPI container alongside scheduler; Entra auth; single POST `/offboard`.
4. **State purge helper** — `purge_external_id_from_state(config, external_id)` in `delta.py` (shared by admin and delete service).

---

## Suggested Implementation Phases

### Phase A — Research and API spike (no production change)

- Obtain Everbridge API credentials and test delete in sandbox.
- Document exact API calls for get-by-external-id and delete.
- Decide SFTP vs API for deletes.

### Phase B — Delete via CLI (internal IT tool)

- Implement `ApiTransport.delete_contact` (or SFTP delete CSV).
- Add `admin.py delete-external-id` with confirmation prompt.
- Deletion audit log + Teams notification on completion.

### Phase C — HR-facing UI

- Internal form or webapp with Entra SSO.
- Confirmation step showing contact name.
- Rate limiting and role checks.

### Phase D — Optional API upsert migration

- Implement `ApiTransport.upsert_contacts` for scheduled sync.
- Run parallel or cut over from SFTP after validation.

---

## What We Are Not Building Now

- No Everbridge API client implementation
- No delete endpoints or HR webapp
- No changes to Power Automate or the registration Form
- No automatic offboarding triggers (e.g. from HRIS) — manual HR action first

See [ROADMAP.md](../ROADMAP.md) Phase 3 for the checkbox backlog tied to this document.
