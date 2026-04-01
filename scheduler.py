from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import logging
import sys
from main import main as run_upload

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("SFTP-Scheduler")

def job():
    logger.info("Executing scheduled upload task...")
    try:
        run_upload()
        logger.info("Scheduled task completed successfully.")
    except Exception as e:
        logger.error(f"Error during scheduled task: {e}")

if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone=pytz.timezone("America/New_York"))
    
    # Schedule for every Friday at 10:00 AM Eastern
    trigger = CronTrigger(day_of_week='fri', hour=10, minute=0)
    scheduler.add_job(job, trigger, id='everbridge_upload')

    logger.info("Scheduler started. Task scheduled for Fridays at 10:00 AM ET.")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down...")
