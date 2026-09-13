import argparse
import json

from . import backup as backup_mod
from . import restore as restore_mod
from .config import Config
from .docker_ops import get_client
from .storage import Storage


def main():
    p = argparse.ArgumentParser(prog="dockguard", description="DockGuard – Unraid Container Backup")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Zeigt die Konfiguration und Erreichbarkeit des Ziels")

    b = sub.add_parser("backup", help="Sichert Container")
    b.add_argument("--container", action="append", help="Container-Name (mehrfach möglich)")
    b.add_argument("--all", action="store_true", help="Alle Container sichern")

    r = sub.add_parser("restore", help="Stellt einen Container wieder her")
    r.add_argument("container", help="Container-Name")
    r.add_argument("--backup-id", help="Bestimmtes Backup (Standard: neuestes)")
    r.add_argument("--overwrite", action="store_true", help="Existierenden Container ersetzen")

    sub.add_parser("list", help="Listet alle verfügbaren Backups im Speicherziel")

    d = sub.add_parser("delete", help="Löscht ein einzelnes Backup")
    d.add_argument("container", help="Container-Name")
    d.add_argument("backup-id", help="Backup-Dateiname (siehe 'list')")

    args = p.parse_args()
    cfg = Config()
    cfg.validate()
    storage = Storage(cfg)
    log = lambda m: print(m, flush=True)

    if args.cmd == "status":
        print(f"Typ:        {cfg.backup_type}")
        print(f"Ziel:       {cfg.remote_base}")
        print(f"Plan:       {cfg.schedule} (UTC)")
        print(f"Behalten:   {cfg.keep or 'alle'}")
        print(f"Parallel:   {cfg.parallel}")
        print(f"Limit:      {cfg.bwlimit or 'unbegrenzt'}")
        print(f"Verifizier: {'an' if cfg.verify_upload else 'aus'}")
        print(f"Erreichbar: {storage.test()}")
    elif args.cmd == "backup":
        client = get_client(cfg.docker_host)
        if args.all or not args.container:
            results = backup_mod.backup_all(cfg, client, storage, log=log)
        else:
            results = [
                backup_mod.backup_container(client, cfg, storage, n, log=log)
                for n in args.container
            ]
        print(json.dumps(results, indent=2))
    elif args.cmd == "restore":
        client = get_client(cfg.docker_host)
        result = restore_mod.restore(
            storage, cfg, client, args.container, args.backup_id, args.overwrite, log=log
        )
        print(json.dumps(result, indent=2))
    elif args.cmd == "list":
        print(json.dumps(restore_mod.available_backups(storage), indent=2))
    elif args.cmd == "delete":
        storage.delete_backup(args.container, args.backup_id)
        print(f"Backup '{args.backup_id}' von '{args.container}' gelöscht")


if __name__ == "__main__":
    main()
