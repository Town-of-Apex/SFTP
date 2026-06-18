# Initial Bulk-Load Strategy

When the Form is sent org-wide, response volume may spike well above normal weekly deltas. Use this plan for the rollout window.

## Recommended Timeline

| Phase | Duration | Sync frequency | Notes |
|-------|----------|----------------|-------|
| Pilot | 1–2 weeks | Daily (or on-demand) | 10–20 staff in one department |
| Rollout window | 2–4 weeks | Daily at 6:00 AM ET | After org-wide Form send |
| Steady state | Ongoing | Weekly (Friday 10:00 AM ET) | Default production schedule |

## Configuration for Rollout Window

Temporarily override schedule via `.env`:

```env
SYNC_DAY_OF_WEEK=mon,tue,wed,thu,fri
SYNC_HOUR=6
SYNC_MINUTE=0
```

Or trigger on-demand without waiting for the scheduler:

```bash
docker compose exec sftp-uploader uv run python scheduler.py --run-now
# or
docker compose exec sftp-uploader uv run python main.py
```

## First-Wave Actions

1. **Send Form org-wide** (comms/HR coordinated).
2. **Wait 24–48 hours** for initial response wave.
3. **Run manual sync** during business hours; do not wait until Friday.
4. **Monitor Everbridge admin UI** for import success and contact counts.
5. **Check `sent_files/` and logs** for row counts vs. expected submissions.
6. **Run daily** until response rate plateaus (typically 2–4 weeks).
7. **Revert to weekly schedule** once >90% of expected staff have registered or deadline passes.

## Capacity Checks

- [ ] Confirm with Everbridge there is no per-upload row limit that could block a large delta file
- [ ] Ensure Docker host has disk space for `sent_files/` and `failed_uploads/` archives
- [ ] Confirm outbound SFTP (port 22) is allowed from the host

## If Volume Is Very Large (1000+ rows in one batch)

- Run sync during off-peak hours
- Keep IT and Everbridge admin available for the first bulk upload
- Use `admin.py preview` before the first production run to confirm pending row count
- If Everbridge processing is slow, avoid running multiple syncs the same hour

## Returning to Steady State

```env
SYNC_DAY_OF_WEEK=fri
SYNC_HOUR=10
SYNC_MINUTE=0
```

Restart the container after changing schedule env vars:

```bash
docker compose up -d
```
