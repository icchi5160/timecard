import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def create_scheduler(notifications, sync):
    scheduler = BackgroundScheduler(timezone="UTC")

    def tick():
        # Failures in one worker must not block the other.
        for job in (notifications.tick, sync.process):
            try:
                job()
            except Exception:
                logger.error("Scheduled job failed: %s", job.__name__)

    scheduler.add_job(tick, "interval", minutes=1, max_instances=1, coalesce=True)
    return scheduler, tick
