"""Backup-Logik: Container-Inspect + Unraid-Template + alle Mounts als .tar.

Mehrere Container können parallel gesichert werden (cfg.parallel). Der Upload
läuft über storage.upload() atomar (siehe app/storage.py).
"""
import datetime as dt
import json
import os
import shutil
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .docker_ops import find_template, get_client

# Pfade, die nie gesichert werden (System-/Laufzeitpfade)
SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/etc", "/var/run", "/run")


def _should_skip(source):
    if not source or source == "/":
        return True
    for prefix in SKIP_PREFIXES:
        if source == prefix or source.startswith(prefix + "/"):
            return True
    return False


def _excluded_mount(source, destination, patterns):
    haystack = f"{source or ''} {destination or ''}".lower()
    return any(p.lower() in haystack for p in patterns if p)


def _tar_dir(src, dest_file, log):
    count = 0
    with tarfile.open(dest_file, "w:gz") as tf:
        for root, dirs, files in os.walk(src):
            entries = list(files)
            for d in dirs:
                if os.path.islink(os.path.join(root, d)):
                    entries.append(d)
            for fn in entries:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, src)
                try:
                    tf.add(fp, arcname=rel, recursive=False)
                    count += 1
                except (OSError, tarfile.TarError) as exc:
                    log(f"  Warnung: '{rel}' wurde übersprungen ({exc})")
    return count


def backup_container(client, cfg, storage, name, log=print):
    """Sichert einen Container: Inspect-JSON + Template + alle Mounts."""
    t0 = time.monotonic()
    api = client.api
    ins = api.inspect_container(name)
    cname = (ins.get("Name") or "").lstrip("/") or name
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_name = f"{cname}_{ts}.tar"

    stage = tempfile.mkdtemp(prefix=f"cb_{cname}_", dir=cfg.staging_dir)
    try:
        tpl = find_template(cfg.templates_dirs, cname)
        if tpl:
            log(f"Template gefunden: {tpl['filename']}")
        else:
            log(f"Kein Unraid-Template für '{cname}' gefunden (nur Docker-Konfiguration gesichert)")

        vol_dir = os.path.join(stage, "volumes")
        os.makedirs(vol_dir, exist_ok=True)
        mounts_info = []
        total_bytes = 0
        files_total = 0
        for m in ins.get("Mounts") or []:
            src = m.get("Source")
            mtype = m.get("Type")
            dst = m.get("Destination")
            if mtype == "tmpfs" or _should_skip(src):
                continue
            if _excluded_mount(src, dst, cfg.exclude_mounts):
                log(f"  Mount '{dst}' übersprungen (ausgeschlossen)")
                continue
            if not os.path.exists(src):
                log(f"  Mount '{src}' existiert nicht, übersprungen")
                continue
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in src)
            vol_file = f"{safe}.tar.gz"
            log(f"Sichere '{src}' -> '{dst}'")
            n = _tar_dir(src, os.path.join(vol_dir, vol_file), log)
            size = os.path.getsize(os.path.join(vol_dir, vol_file))
            mounts_info.append(
                {
                    "source": src,
                    "destination": dst,
                    "type": mtype,
                    "file": vol_file,
                    "files": n,
                    "size_bytes": size,
                }
            )
            total_bytes += size
            files_total += n

        labels = (ins.get("Config") or {}).get("Labels") or {}
        meta = {
            "container": cname,
            "image": (ins.get("Config") or {}).get("Image"),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "started_at": (ins.get("State") or {}).get("StartedAt") or "",
            "tool_version": __version__,
            "state": (ins.get("State") or {}).get("Status"),
            "template": tpl,
            "mounts": mounts_info,
            "data_bytes": total_bytes,
            "data_files": files_total,
            "unraid": any(k.startswith("io.unraid") for k in labels),
            "inspect": ins,
        }
        with open(os.path.join(stage, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)

        archive = os.path.join(stage, archive_name)
        with tarfile.open(archive, "w") as tf:
            tf.add(os.path.join(stage, "meta.json"), arcname="meta.json")
            tf.add(vol_dir, arcname="volumes")

        size = os.path.getsize(archive)
        log(f"Uploade '{archive_name}' ({size // (1024 * 1024)} MB)")
        storage.upload(archive, cname)
        _apply_retention(storage, cname, cfg.keep, log)
        return {
            "container": cname,
            "archive": archive_name,
            "size_bytes": size,
            "data_bytes": total_bytes,
            "data_files": files_total,
            "mounts": mounts_info,
            "elapsed_seconds": round(time.monotonic() - t0, 1),
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _apply_retention(storage, container, keep, log):
    if not keep or keep <= 0:
        return
    backups = storage.list_backups(container)
    while len(backups) > keep:
        old = backups.pop(0)
        log(f"Altes Backup gelöscht: {old['id']}")
        storage.delete_backup(container, old["id"])


def backup_all(cfg, client, storage, log=print):
    all_containers = client.containers.list(all=True)
    for c in all_containers:
        if c.name in cfg.exclude:
            log(f"Überspringe '{c.name}' (ausgeschlossen)")
    names = [c.name for c in all_containers if c.name not in cfg.exclude]

    def one(name):
        log(f"--- Backup: {name} ---")
        try:
            own_client = get_client(cfg.docker_host)  # thread-eigener Client
            return backup_container(own_client, cfg, storage, name, log=log)
        except Exception as exc:
            log(f"Fehler bei '{name}': {exc}")
            return {"container": name, "error": str(exc)}

    workers = max(1, min(cfg.parallel, len(names) or 1))
    results = []
    if workers <= 1:
        results = [one(n) for n in names]
    else:
        log(f"Starte Backups parallel ({workers} gleichzeitig)")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, n): n for n in names}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    results.append({"container": futures[fut], "error": str(exc)})
        results.sort(key=lambda r: r.get("container", ""))
    return results
