import datetime as dt
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import backup as backup_mod
from . import restore as restore_mod
from .config import Config
from .docker_ops import get_client
from .scheduler import start_scheduler
from .storage import Storage, StorageError

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

cfg = Config()
try:
    cfg.validate()
except ValueError as exc:
    raise SystemExit(f"Konfigurationsfehler: {exc}")

storage = Storage(cfg)
app = FastAPI(title="Unraid Container Backup")


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def submit(self, title, fn):
        jid = uuid.uuid4().hex[:8]
        job = {
            "id": jid,
            "title": title,
            "status": "running",
            "log": [],
            "result": None,
            "error": None,
            "started": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        with self.lock:
            self.jobs[jid] = job

        def runner():
            def log(msg):
                line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}"
                job["log"].append(line)
                print(line, flush=True)

            try:
                job["result"] = fn(log)
                job["status"] = "done"
            except Exception as exc:
                job["status"] = "error"
                job["error"] = str(exc)
                log(f"FEHLER: {exc}")

        threading.Thread(target=runner, daemon=True).start()
        return jid

    def recent(self, limit=30):
        with self.lock:
            items = list(self.jobs.values())
        items.sort(key=lambda j: j["started"], reverse=True)
        return items[:limit]


job_manager = JobManager()

_backup_cache = {}
_cache_lock = threading.Lock()


def _backups_for(container):
    now = dt.datetime.now(dt.timezone.utc)
    with _cache_lock:
        hit = _backup_cache.get(container)
        if hit and (now - hit[0]).total_seconds() < 30:
            return hit[1]
    try:
        bl = storage.list_backups(container)
    except StorageError:
        bl = []
    with _cache_lock:
        _backup_cache[container] = (now, bl)
    return bl


def _docker_client():
    try:
        return get_client(cfg.docker_host)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Docker nicht erreichbar: {exc}")


@app.on_event("startup")
def startup():
    ok = storage.test()
    print(f"Speicherziel: {cfg.remote_base} ({cfg.backup_type}) - erreichbar: {ok}", flush=True)
    try:
        get_client(cfg.docker_host).ping()
        print("Docker-API verbunden", flush=True)
    except Exception as exc:
        print(f"ACHTUNG: Docker nicht erreichbar: {exc}", flush=True)
    start_scheduler(cfg, storage, job_manager)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status():
    return {
        "storage_type": cfg.backup_type,
        "remote": cfg.remote_base,
        "storage_ok": storage.test(),
        "schedule": cfg.schedule,
        "keep": cfg.keep,
    }


@app.get("/api/containers")
def api_containers():
    client = _docker_client()
    out = []
    for c in client.containers.list(all=True):
        bl = _backups_for(c.name)
        last = max(bl, key=lambda b: b["id"]) if bl else None
        out.append(
            {
                "name": c.name,
                "image": (c.image.tags[0] if c.image.tags else c.image.short_id),
                "state": c.status,
                "last_backup": last["id"] if last else None,
            }
        )
    return out


@app.get("/api/backups")
def api_backups():
    try:
        return restore_mod.available_backups(storage)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class BackupReq(BaseModel):
    containers: list[str] | None = None
    all: bool = False


@app.post("/api/backup")
def api_backup(req: BackupReq):
    def work(log):
        client = get_client(cfg.docker_host)
        if req.all or not req.containers:
            return backup_mod.backup_all(cfg, client, storage, log=log)
        return [
            backup_mod.backup_container(client, cfg, storage, n, log=log) for n in req.containers
        ]

    return {"job": job_manager.submit("Backup", work)}


class RestoreReq(BaseModel):
    container: str
    backup_id: str | None = None
    overwrite: bool = False


@app.post("/api/restore")
def api_restore(req: RestoreReq):
    def work(log):
        client = get_client(cfg.docker_host)
        return restore_mod.restore(
            storage, cfg, client, req.container, req.backup_id, req.overwrite, log=log
        )

    return {"job": job_manager.submit(f"Wiederherstellung: {req.container}", work)}


@app.get("/api/jobs")
def api_jobs():
    return job_manager.recent()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=cfg.web_port)
