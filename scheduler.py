"""APScheduler entry point for scheduled sync runs."""

from __future__ import annotations

import argparse
import logging
import sys

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import load_config
from src.logging_config import setup_logging
from src.pipeline import run_sync

logger = logging.getLogger("everbridge-sync.scheduler")


def job() -> None:
    logger.info("Executing scheduled upload task...")
    result = run_sync()
    if result.status == "failed":
        logger.error("Scheduled task failed (run_id=%s).", result.sync_run_id)
    elif result.status == "success":
        logger.info(
            "Scheduled task completed: uploaded %s row(s).",
            result.rows_uploaded,
        )
    else:
        logger.info("Scheduled task completed: no action required.")


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Everbridge sync scheduler")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run one sync immediately and exit (do not start scheduler).",
    )
    args = parser.parse_args()

    if args.run_now:
        job()
        return

    config = load_config()
    scheduler = BlockingScheduler(timezone=pytz.timezone(config.sync_timezone))
    days = [day.strip() for day in config.sync_day_of_week.split(",") if day.strip()]

    for index, day in enumerate(days):
        trigger = CronTrigger(
            day_of_week=day,
            hour=config.sync_hour,
            minute=config.sync_minute,
        )
        scheduler.add_job(
            job,
            trigger,
            id=f"everbridge_upload_{index}_{day}",
            misfire_grace_time=3600,
        )

    logger.info(
        "Scheduler started. Task scheduled for %s at %02d:%02d %s.",
        ", ".join(days),
        config.sync_hour,
        config.sync_minute,
        config.sync_timezone,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down...")


if __name__ == "__main__":
    main()
