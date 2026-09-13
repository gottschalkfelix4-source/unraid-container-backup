"""Restore-Logik: One-Click-Wiederherstellung eines (auch gelöschten) Containers.

Ablauf:
1. Backup herunterladen (neuestes oder gewähltes)
2. Daten an die Originalpfade extrahieren (mit Traversal-Schutz)
3. Unraid-Template zurück nach templates-user schreiben
4. Image sicherstellen (Pull falls nötig)
5. Container exakt wie vorher neu anlegen und starten (inkl. fehlender Netze)
"""
import json
import os
import shutil
import tarfile
import tempfile
import time

from .docker_ops import ensure_image, recreate_container
from .storage import StorageError


def _keep(member, *args):
    # Eigene Backups sind vertrauenswürdig: alles erhalten (Rechte, Owner)
    return member


def _safe_extract(tf, dest, log):
    """Extrahiert nur meta.json und volumes/* – sicher gegen Pfad-Traversal.

    Entpackt ausschließlich reguläre Dateien mit erlaubtem Namen; fremde oder
    unsichere Einträge werden übersprungen statt extrahiert.
    """
    dest_real = os.path.realpath(dest)
    count = 0
    for member in tf.getmembers():
        name = member.name
        if not (name == "meta.json" or name.startswith("volumes/")):
            log(f"  Überspringe fremdes Archiv-Element '{name}'")
            continue
        if name.startswith("/") or ".." in name.split("/"):
            log(f"  Überspringe unsicheren Pfad '{name}'")
            continue
        target = os.path.realpath(os.path.join(dest, name))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            log(f"  Überspringe Ziel außerhalb des Verzeichnisses '{name}'")
            continue
        if not member.isfile():
            continue  # Verzeichnisse entstehen automatisch
        tf.extract(member, dest, filter=_keep)
        count += 1
    return count


def available_backups(storage):
    result = []
    for c in storage.list_containers():
        try:
            result.append({"container": c, "backups": storage.list_backups(c)})
        except StorageError:
            result.append({"container": c, "backups": []})
    return result


def restore(storage, cfg, client, container, backup_id=None, overwrite=False, log=print):
    """Stellt einen (auch komplett gelöschten) Container wieder her:
    Daten an den Originalpfaden + Template + Container neu angelegt und gestartet."""
    t0 = time.monotonic()
    backups = storage.list_backups(container)
    if not backups:
        raise StorageError(f"Kein Backup für '{container}' gefunden")
    if backup_id is None:
        backup_id = max(b["id"] for b in backups)
    elif not any(b["id"] == backup_id for b in backups):
        raise StorageError(f"Backup '{backup_id}' für '{container}' nicht gefunden")

    stage = tempfile.mkdtemp(prefix=f"cr_{container}_", dir=cfg.staging_dir)
    try:
        log(f"Lade Backup '{backup_id}' herunter ...")
        storage.download(f"{container}/{backup_id}", stage)
        with tarfile.open(os.path.join(stage, backup_id), "r") as tf:
            _safe_extract(tf, stage, log)
        with open(os.path.join(stage, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)

        api = client.api
        existing = None
        try:
            existing = api.inspect_container(container)
        except Exception:
            pass
        if existing:
            if not overwrite:
                raise StorageError(
                    f"Container '{container}' existiert bereits. Bitte 'überschreiben' bestätigen."
                )
            log(f"Stoppe und entferne bestehenden Container '{container}' ...")
            try:
                client.containers.get(container).stop(timeout=30)
            except Exception:
                pass
            api.remove_container(container, force=True)

        # 1. Daten an den Originalpfaden wiederherstellen
        vol_dir = os.path.join(stage, "volumes")
        restored = []
        for mi in meta.get("mounts") or []:
            vf_name = mi.get("file", "")
            if os.path.basename(vf_name) != vf_name:
                raise StorageError(f"Ungültiger Mount-Dateiname im Backup: {vf_name!r}")
            vf = os.path.join(vol_dir, vf_name)
            if not os.path.exists(vf):
                log(f"  Daten für '{mi.get('source')}' im Backup nicht vorhanden, übersprungen")
                continue
            src = mi["source"]
            if not src or not os.path.isabs(src):
                log(f"  Überspringe ungültigen Quellpfad '{src}'")
                continue
            log(f"Stelle Daten wieder her: '{src}'")
            os.makedirs(src, exist_ok=True)
            with tarfile.open(vf, "r:gz") as tf:
                tf.extractall(src, filter=_keep)
            restored.append({"source": src, "destination": mi.get("destination")})
        log(f"{len(restored)} Mount(s) wiederhergestellt")

        # 2. Unraid-Template wiederherstellen (erscheint danach in der Docker-UI)
        tpl = meta.get("template")
        tpl_restored = False
        if tpl:
            tpl_dir = (cfg.templates_dirs or [cfg.templates_dir])[0]
            os.makedirs(tpl_dir, exist_ok=True)
            tpath = os.path.join(tpl_dir, os.path.basename(tpl["filename"]))
            with open(tpath, "w", encoding="utf-8") as f:
                f.write(tpl["content"])
            log(f"Template wiederhergestellt: {os.path.basename(tpl['filename'])}")
            tpl_restored = True

        # 3. Image sicherstellen
        log(f"Stelle Image sicher: {meta.get('image')}")
        ensure_image(api, meta["image"], log)

        # 4. Container exakt wie vorher neu anlegen und starten
        recreate_container(api, container, meta["inspect"], log)
        log(f"Container '{container}' erfolgreich wiederhergestellt")
        return {
            "container": container,
            "backup": backup_id,
            "template_restored": tpl_restored,
            "mounts_restored": len(restored),
            "image": meta.get("image"),
            "elapsed_seconds": round(time.monotonic() - t0, 1),
        }
    finally:
        shutil.rmtree(stage, ignore_errors=True)
