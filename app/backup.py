import datetime as dt
import json
import os
import shutil
import tarfile
import tempfile

from .docker_ops import find_template

# Pfade, die nie gesichert werden (System-/Laufzeitpfade)
SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/etc", "/var/run", "/run")


def _should_skip(source):
    if not source or source == "/":
        return True
    for prefix in SKIP_PREFIXES:
        if source == prefix or source.startswith(prefix + "/"):
            return True
    return False


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
        for m in ins.get("Mounts") or []:
            src = m.get("Source")
            mtype = m.get("Type")
            if mtype == "tmpfs" or _should_skip(src):
                continue
            if not os.path.exists(src):
                log(f"  Mount '{src}' existiert nicht, übersprungen")
                continue
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in src)
            vol_file = f"{safe}.tar.gz"
            log(f"Sichere '{src}' -> '{m.get('Destination')}'")
            n = _tar_dir(src, os.path.join(vol_dir, vol_file), log)
            mounts_info.append(
                {
                    "source": src,
                    "destination": m.get("Destination"),
                    "type": mtype,
                    "file": vol_file,
                    "files": n,
                    "size_bytes": os.path.getsize(os.path.join(vol_dir, vol_file)),
                }
            )

        meta = {
            "container": cname,
            "image": (ins.get("Config") or {}).get("Image"),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "state": (ins.get("State") or {}).get("Status"),
            "template": tpl,
            "mounts": mounts_info,
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
            "mounts": mounts_info,
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
    results = []
    for c in client.containers.list(all=True):
        if c.name in cfg.exclude:
            log(f"Überspringe '{c.name}' (ausgeschlossen)")
            continue
        log(f"--- Backup: {c.name} ---")
        try:
            results.append(backup_container(client, cfg, storage, c.name, log=log))
        except Exception as exc:
            log(f"Fehler bei '{c.name}': {exc}")
            results.append({"container": c.name, "error": str(exc)})
    return results
