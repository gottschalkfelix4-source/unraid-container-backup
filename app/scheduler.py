from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import backup as backup_mod
from .docker_ops import get_client


def start_scheduler(cfg, storage, job_manager):
    def run():
        def work(log):
            client = get_client(cfg.docker_host)
            return backup_mod.backup_all(cfg, client, storage, log=log)

        job_manager.submit("Geplantes Backup", work)

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(
        run,
        CronTrigger.from_crontab(cfg.schedule),
        id="scheduled-backup",
        replace_existing=True,
    )
    sched.start()
    print(f"Scheduler gestartet: '{cfg.schedule}' (UTC)", flush=True)
    return sched
