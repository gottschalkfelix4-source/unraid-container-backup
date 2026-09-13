"""Geplante Backups über APScheduler (Cron, Zeitzone UTC)."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import backup as backup_mod
from .docker_ops import get_client

JOB_ID = "scheduled-backup"


def start_scheduler(cfg, storage, job_manager):
    def run():
        def work(log):
            client = get_client(cfg.docker_host)
            return backup_mod.backup_all(cfg, client, storage, log=log)

        job_manager.submit("Geplantes Backup", work, kind="scheduled")

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        run,
        CronTrigger.from_crontab(cfg.schedule, timezone="UTC"),
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    sched.start()
    print(f"Scheduler gestartet: '{cfg.schedule}' (UTC)", flush=True)
    return sched


def next_run(sched):
    """Nächster geplanter Lauf als ISO-String (oder None)."""
    if sched is None:
        return None
    job = sched.get_job(JOB_ID)
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None
