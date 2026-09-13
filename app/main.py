import base64
import datetime as dt
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from . import backup as backup_mod
from . import restore as restore_mod
from .config import Config
from .docker_ops import get_client
from .scheduler import JOB_ID, next_run, start_scheduler
from .storage import Storage, StorageError

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

cfg = Config()
try:
    cfg.validate()
except ValueError as exc:
    # Nicht fatal: Die Web-UI kann die Einstellungen nachträglich vervollständigen.
    print(f"ACHTUNG: Konfiguration unvollständig ({exc}). Bitte über die Web-UI eintragen.", flush=True)

storage = Storage(cfg)
scheduler = None
app = FastAPI(title="Unraid Container Backup", version=__version__)

_BACKUP_TS_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.tar$")


# ---------------------------------------------------------------------------
# Optionaler Basicauth (WEB_USER/WEB_PASSWORD – bewusst nicht über die UI
# änderbar, damit sich die Weboberfläche nicht selbst aussperren kann)
# ---------------------------------------------------------------------------
if cfg.web_password:
    _AUTH_EXPECTED = f"{cfg.web_user}:{cfg.web_password}".encode()

    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                ok = secrets.compare_digest(base64.b64decode(header[6:].strip()), _AUTH_EXPECTED)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="container-backup"'},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Job-Manager: Hintergrund-Jobs (Backups/Restores) mit Log, Status und Dauer
# ---------------------------------------------------------------------------
class JobManager:
    def __init__(self, limit=200):
        self.limit = limit
        self.lock = threading.Lock()
        self.jobs = {}
        self.order = []

    def submit(self, title, fn, kind="job"):
        jid = uuid.uuid4().hex[:8]
        job = {
            "id": jid,
            "title": title,
            "kind": kind,
            "status": "running",
            "log": [],
            "result": None,
            "error": None,
            "started": dt.datetime.now(dt.timezone.utc).isoformat(),
            "finished": None,
            "duration": None,
        }
        with self.lock:
            self.jobs[jid] = job
            self.order.append(jid)
            while len(self.jobs) > self.limit:
                old = self.order.pop(0)
                self.jobs.pop(old, None)

        def runner():
            t0 = time.monotonic()

            def log(msg):
                line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}"
                job["log"].append(line)
                if len(job["log"]) > 800:  # Ringbuffer: nie unbegrenzt wachsen
                    del job["log"][: len(job["log"]) - 800]
                print(line, flush=True)

            try:
                job["result"] = fn(log)
                job["status"] = "done"
            except Exception as exc:
                job["status"] = "error"
                job["error"] = str(exc)
                log(f"FEHLER: {exc}")
            job["finished"] = dt.datetime.now(dt.timezone.utc).isoformat()
            job["duration"] = round(time.monotonic() - t0, 1)

        threading.Thread(target=runner, daemon=True).start()
        return jid

    def recent(self, limit=30):
        with self.lock:
            items = list(self.jobs.values())
        items.sort(key=lambda j: j["started"], reverse=True)
        return items[:limit]


job_manager = JobManager()

# ---------------------------------------------------------------------------
# Caching (Remote-Aufrufe sind teuer: rclone-Listing bzw. Docker-API)
# ---------------------------------------------------------------------------
_backup_cache = {}
_cache_lock = threading.Lock()

_groups_cache = {"ts": 0.0, "value": None}
_status_cache = {"ts": 0.0, "value": None}


def _clear_caches():
    with _cache_lock:
        _backup_cache.clear()
    _groups_cache["ts"] = 0
    _status_cache["ts"] = 0


def _backups_for(container):
    now = time.monotonic()
    with _cache_lock:
        hit = _backup_cache.get(container)
        if hit and (now - hit[0]) < 30:
            return hit[1]
    try:
        bl = storage.list_backups(container)
    except StorageError:
        bl = []
    with _cache_lock:
        _backup_cache[container] = (now, bl)
    return bl


def _all_backups():
    now = time.monotonic()
    if now - _groups_cache["ts"] < 30 and _groups_cache["value"] is not None:
        return _groups_cache["value"]
    try:
        value = restore_mod.available_backups(storage)
    except StorageError:
        value = []
    _groups_cache["ts"] = now
    _groups_cache["value"] = value
    return value


def _backup_epoch(backup):
    """Bestmöglicher Zeitstempel eines Backups (mtime > ID-Zeitstempel)."""
    mtime = (backup.get("mtime") or "").strip()
    if mtime:
        try:
            return dt.datetime.fromisoformat(mtime.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    m = _BACKUP_TS_RE.search(backup["id"])
    if m:
        try:
            parts = tuple(int(g) for g in m.groups())
            return dt.datetime(*parts, tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return 0.0


def _build_status():
    """Status-Payload inkl. Statistiken über alle Backups (gedrosselt)."""
    ok = storage.test()
    groups = _all_backups()
    container_count = len(groups)
    backup_count = 0
    total_bytes = 0
    last = None  # (epoch, container, backup_id)
    for g in groups:
        for b in g["backups"]:
            backup_count += 1
            total_bytes += b.get("size", 0) or 0
            ep = _backup_epoch(b)
            if last is None or ep > last[0]:
                last = (ep, g["container"], b["id"])
    return {
        "version": __version__,
        "storage_type": cfg.backup_type,
        "remote": cfg.remote_base,
        "storage_ok": ok,
        "verify_uploads": cfg.verify_upload,
        "schedule": cfg.schedule,
        "next_run": next_run(scheduler),
        "keep": cfg.keep,
        "parallel": cfg.parallel,
        "stats": {
            "containers": container_count,
            "backups": backup_count,
            "total_bytes": total_bytes,
            "last_backup_ts": (
                dt.datetime.fromtimestamp(last[0], tz=dt.timezone.utc).isoformat() if last else None
            ),
            "last_backup_container": last[1] if last else None,
            "last_backup_id": last[2] if last else None,
        },
    }


def _status_payload(force=False):
    now = time.monotonic()
    if not force and now - _status_cache["ts"] < 30 and _status_cache["value"] is not None:
        payload = _status_cache["value"]
        payload["next_run"] = next_run(scheduler)
        return payload
    payload = _build_status()
    _status_cache["ts"] = now
    _status_cache["value"] = payload
    return payload


def _docker_client():
    try:
        return get_client(cfg.docker_host)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Docker nicht erreichbar: {exc}")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    global scheduler
    ok = storage.test()
    print(f"Speicherziel: {cfg.remote_base} ({cfg.backup_type}) - erreichbar: {ok}", flush=True)
    try:
        get_client(cfg.docker_host).ping()
        print("Docker-API verbunden", flush=True)
    except Exception as exc:
        print(f"ACHTUNG: Docker nicht erreichbar: {exc}", flush=True)
    scheduler = start_scheduler(cfg, storage, job_manager)


def _reschedule():
    """Cron-Job nach Änderung des Zeitplans neu planen."""
    if scheduler is None:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger

        scheduler.reschedule_job(JOB_ID, trigger=CronTrigger.from_crontab(str(cfg.schedule), timezone="UTC"))
        print(f"Scheduler neu geplant: '{cfg.schedule}' (UTC)", flush=True)
    except Exception as exc:
        print(f"Scheduler konnte nicht neu geplant werden: {exc}", flush=True)


# ---------------------------------------------------------------------------
# UI & Health
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def api_health():
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def api_status():
    return _status_payload()


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def api_settings_get():
    d = cfg.to_dict()
    d["storage_ok"] = storage.test()
    d["remote"] = cfg.remote_base
    return d


@app.post("/api/settings")
def api_settings_save(req: dict):
    global storage
    try:
        cfg.update(req)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Speichern fehlgeschlagen: {exc}")
    storage = Storage(cfg)
    _clear_caches()
    _reschedule()
    return {"ok": True, "settings": cfg.to_dict(), "storage_ok": storage.test()}


@app.post("/api/settings/test")
def api_settings_test(req: dict):
    """Testet die Verbindung mit den übergebenen Werten, ohne sie zu speichern."""
    try:
        tmp = Config.from_dict(req)
        tmp.validate()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    st = Storage(tmp)
    return {"ok": st.test()}


# ---------------------------------------------------------------------------
# Container & Backups
# ---------------------------------------------------------------------------
@app.get("/api/containers")
def api_containers():
    client = _docker_client()
    out = []
    try:
        containers = client.containers.list(all=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Docker-API-Fehler: {exc}")
    for c in containers:
        bl = _backups_for(c.name)
        last = max(bl, key=lambda b: _backup_epoch(b)) if bl else None
        out.append(
            {
                "name": c.name,
                "image": (c.image.tags[0] if c.image.tags else c.image.short_id),
                "state": c.status,
                "running": c.status in ("running", "restarting"),
                "backup_count": len(bl),
                "last_backup": last["id"] if last else None,
                "last_backup_size": last.get("size", 0) if last else None,
            }
        )
    return out


@app.get("/api/backups")
def api_backups():
    return _all_backups()


@app.delete("/api/backups/{container}/{backup_id}")
def api_backup_delete(container: str, backup_id: str):
    try:
        storage.delete_backup(container, backup_id)
    except StorageError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    _clear_caches()
    return {"ok": True, "container": container, "backup_id": backup_id}


# ---------------------------------------------------------------------------
# Backup & Restore
# ---------------------------------------------------------------------------
class BackupReq(BaseModel):
    containers: list[str] | None = None
    all: bool = False


@app.post("/api/backup")
def api_backup(req: BackupReq):
    def work(log):
        client = get_client(cfg.docker_host)
        if req.all or not req.containers:
            return backup_mod.backup_all(cfg, client, storage, log=log)
        results = []
        for n in req.containers:
            try:
                results.append(backup_mod.backup_container(client, cfg, storage, n, log=log))
            except Exception as exc:
                results.append({"container": n, "error": str(exc)})
        return results

    title = "Backup: alle Container" if (req.all or not req.containers) else (
        "Backup: " + ", ".join(req.containers)
    )
    return {"job": job_manager.submit(title, work, kind="backup")}


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

    return {"job": job_manager.submit(f"Wiederherstellung: {req.container}", work, kind="restore")}


@app.get("/api/jobs")
def api_jobs():
    return job_manager.recent()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=cfg.web_port)
