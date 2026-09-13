import json
import os
import shutil
import tarfile
import tempfile

from .docker_ops import ensure_image, recreate_container
from .storage import StorageError


def _keep(member, *args):
    # Eigene Backups sind vertrauenswürdig: alles erhalten (Rechte, Owner)
    return member


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
            tf.extractall(stage, filter=_keep)
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
        for mi in meta.get("mounts") or []:
            vf = os.path.join(vol_dir, mi["file"])
            if not os.path.exists(vf):
                log(f"  Daten für '{mi['source']}' im Backup nicht vorhanden, übersprungen")
                continue
            src = mi["source"]
            log(f"Stelle Daten wieder her: '{src}'")
            os.makedirs(src, exist_ok=True)
            with tarfile.open(vf, "r:gz") as tf:
                tf.extractall(src, filter=_keep)

        # 2. Unraid-Template wiederherstellen (erscheint danach in der Docker-UI)
        tpl = meta.get("template")
        if tpl:
            os.makedirs(cfg.templates_dir, exist_ok=True)
            tpath = os.path.join(cfg.templates_dir, tpl["filename"])
            with open(tpath, "w", encoding="utf-8") as f:
                f.write(tpl["content"])
            log(f"Template wiederhergestellt: {tpl['filename']}")

        # 3. Image sicherstellen
        log(f"Stelle Image sicher: {meta.get('image')}")
        ensure_image(api, meta["image"], log)

        # 4. Container exakt wie vorher neu anlegen und starten
        recreate_container(api, container, meta["inspect"], log)
        log(f"Container '{container}' erfolgreich wiederhergestellt")
        return {"container": container, "backup": backup_id, "template_restored": bool(tpl)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)
