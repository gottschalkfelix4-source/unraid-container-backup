# Unraid Container Backup

Ein Backup-Tool als Docker-Container für Unraid, das Docker-Container auf einen anderen Server sichert – per **SMB-Share** oder **S3-Storage** – und sie nach einem kompletten Löschen **mit einem Klick wiederherstellt**:

- **Daten** an den Originalpfaden (Bind-Mounts & benannte Volumes)
- **Unraid-Template** (Community Apps) aus `templates-user`
- **Container** selbst: Image, Ports, Environment, Netzwerk, Restart-Policy, Labels – exakt wie vorher

## Funktionsweise

Jedes Backup eines Containers ist eine einzelne `.tar`-Datei im Speicherziel:

```
<ziel>/<container-name>/
└── <container-name>_20260913_030000.tar
    ├── meta.json        # vollständiges docker inspect + Mount-Liste
    └── volumes/
        ├── mnt_user_appdata_jellyfin.tar.gz
        └── ...
```

Das Unraid-Template (XML) wird beim Backup mit in `meta.json` aufgenommen und beim Restore nach `/boot/config/plugins/dockerMan/templates-user/` zurückgeschrieben – der Container erscheint danach wieder in der Unraid-Docker-UI wie vorher.

**Restore-Ablauf (ein Klick in der Web-UI):**
1. Neuestes (oder gewähltes) Backup wird heruntergeladen
2. Daten werden an die Originalpfade extrahiert
3. Template wird wiederhergestellt
4. Image wird bei Bedarf neu gezogen
5. Container wird exakt wie vorher neu angelegt und gestartet

## Installation

### 1. Image bauen

Auf dem Unraid-Host (oder einem anderen Docker-Host):

```bash
./build.sh
# oder: docker build -t unraid-container-backup:latest .
```

### 1b. Unraid-Template (Community Apps)

Ein fertiges Template liegt unter [`template/unraid-container-backup.xml`](template/unraid-container-backup.xml). Installation:

1. Die Datei nach `/boot/config/plugins/dockerMan/templates-user/` kopieren
2. In der Unraid-Docker-UI („Docker“ → „Add Container“) erscheint **container-backup** als Vorlage
3. Image `unraid-container-backup:latest` auswählen, die Zugangsdaten eintragen und loslegen

Alle Optionen (SMB/S3-Ziel, Zeitplan, Aufbewahrung, Parallelität, Ausschlüsse, Login) sind als Felder im Template enthalten. Alternativ lässt sich das Template über den Community-Apps-Modus („Install from file“) importieren.

### 2. Container anlegen

**Variante A – Compose Manager** (empfohlen): `unraid-compose.yml` anpassen und importieren.

**Variante B – Docker-GUI:**

| Feld | Wert |
|---|---|
| Repository | `unraid-container-backup:latest` |
| Container-Name | `container-backup` |
| Host-Port | `8080` → Container-Port `8080` |
| Volume 1 | `/var/run/docker.sock` → `/var/run/docker.sock` |
| Volume 2 | `/mnt/user` → `/mnt/user` |
| Volume 3 | `/boot/config` → `/boot/config` |
| Volume 4 | `/var/lib/docker/volumes` → `/var/lib/docker/volumes` |
| Extra-Parameter | `-e BACKUP_TYPE=smb -e SMB_HOST=... -e SMB_USER=... -e SMB_PASS=... -e SMB_SHARE=... -e BACKUP_SCHEDULE="0 3 * * *"` |

> **Wichtig:** Das Tool braucht Zugriff auf den Docker-Socket (= Root-Rechte auf dem Host) sowie Schreibzugriff auf `/mnt/user`, `/boot/config` und `/var/lib/docker/volumes` für den Restore.

### 3. Konfiguration (Umgebungsvariablen)

| Variable | Standard | Beschreibung |
|---|---|---|
| `SETTINGS_FILE` | `/config/settings.json` | Pfad der Einstellungsdatei (Web-UI) |
| `BACKUP_TYPE` | `smb` | `smb` oder `s3` |
| `SMB_HOST` | – | IP/Hostname des Zielservers |
| `SMB_PORT` | `445` | SMB-Port |
| `SMB_USER` / `SMB_PASS` | – | Zugangsdaten |
| `SMB_SHARE` | – | Name des Shares |
| `SMB_PATH` | `""` | Unterverzeichnis im Share |
| `S3_PROVIDER` | `AWS` | `AWS`, `Minio`, `Garage`, … (rclone-Provider) |
| `S3_ENDPOINT` | – | Endpoint (z. B. Minio/Garage), bei AWS leer lassen |
| `S3_REGION` | `us-east-1` | Region |
| `S3_BUCKET` | – | Bucket-Name |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | – | Zugangsdaten |
| `S3_PATH` | `""` | Unterverzeichnis im Bucket |
| `BACKUP_SCHEDULE` | `0 3 * * *` | Cron-Ausdruck (UTC), z. B. `0 */6 * * *` = alle 6 h |
| `BACKUP_KEEP` | `0` | Backups pro Container behalten (`0` = alle) |
| `BACKUP_PARALLEL` | `2` | Container gleichzeitig sichern (1–16) |
| `EXCLUDE_CONTAINERS` | `container-backup` | Kommagetrennte Namen, die nicht gesichert werden |
| `EXCLUDE_MOUNTS` | `""` | Kommagetrennte Ausdrücke; Mounts, deren Quelle oder Ziel sie enthalten, werden übersprungen (z. B. Caches) |
| `TEMPLATES_DIR` | `/boot/config/plugins/dockerMan/templates-user` | Pfad(e) zu den Unraid-Templates, mehrere kommagetrennt möglich (Restore schreibt ins erste) |
| `RCLONE_TIMEOUT` | `7200` | Timeout pro rclone-Befehl in Sekunden |
| `RCLONE_BWLIMIT` | `""` | Upload-Bandbreitenlimit (rclone-Syntax, z. B. `8M`, `500K`), leer = unbegrenzt |
| `VERIFY_UPLOAD` | `1` | Größencheck des Backups auf dem Ziel nach dem Upload (`0` = aus) |
| `WEB_PORT` | `8080` | Port der Web-UI |
| `WEB_USER` / `WEB_PASSWORD` | – | Optionaler Login für die Web-UI (Basicauth) |

> **Passwörter:** SMB-Passwort und S3-Secret-Key werden in der Web-UI nur
> maskiert angezeigt (`********`) und nie im Klartext an den Browser
> zurückgegeben. Ein gespeicherter Wert bleibt beim Speichern erhalten,
> solange die Maske unverändert zurückgeschickt wird.

## Nutzung

### Web-UI

Nach dem Start unter `http://<unraid-ip>:8080` (optional mit Login, wenn `WEB_PASSWORD` gesetzt ist):

- **Übersicht** – Dashboard mit Kennzahlen (Container, Backups, Speicherbelegung, nächster geplanter Lauf, letztes Backup) und den letzten Aufgaben
- **Container** – alle Container als Karten mit Status, Image, Backup-Zähler; Buttons „Sichern“ bzw. „Wiederherstellen“, Suche und Filter (Alle / Läuft / Gestoppt)
- **Backups** – alle Backups gruppiert nach Container mit Suche; pro Backup „Wiederherstellen“ (ein Klick) oder Löschen
- **Einstellungen** – Ziel (SMB/S3 inkl. Zugangsdaten), Zeitplan mit Presets, Aufbewahrung, parallele Backups, Bandbreitenlimit, Ausschlüsse (Container & Mounts), Template-Pfade, Timeout. Mit **„Verbindung testen“** lässt sich das Ziel vor dem Speichern prüfen; gespeichert wird in `/config/settings.json`
- **Aufgaben** – Live-Log aller laufenden/letzten Jobs mit Filter, Auto-Scroll und aufklappbaren Logs

> **Priorität:** Werte aus der Web-UI (settings.json) überschreiben die Umgebungsvariablen. Die Env-Vars dienen als Defaults beim ersten Start – der Container startet auch ohne Konfiguration, dann einfach alles über die UI eintragen.

> **Robustheit:** Uploads laufen atomar (erst `<name>.tar.part`, dann Umbenennen) und werden optional per Größencheck verifiziert – halbfertige Uploads erscheinen nie als gültiges Backup. Beim Restore werden auch fehlende benutzerdefinierte Docker-Netzwerke anhand des Inspect-JSONs neu angelegt (inkl. Aliase).

### CLI

```bash
docker exec -it container-backup python -m app.cli status
docker exec -it container-backup python -m app.cli list
docker exec -it container-backup python -m app.cli backup --all
docker exec -it container-backup python -m app.cli backup --container jellyfin
docker exec -it container-backup python -m app.cli restore jellyfin            # neuestes Backup
docker exec -it container-backup python -m app.cli restore jellyfin --overwrite # existierenden ersetzen
docker exec -it container-backup python -m app.cli delete jellyfin jellyfin_20260901_030000.tar
```

## Grenzen & Hinweise

- Gesichert werden **Dateien der Mounts**, die Konfiguration und das Template. Das **Image** wird beim Restore bei Bedarf neu gezogen (nicht als Layer gesichert).
- tmpfs-Mounts und Systempfade (`/etc`, `/dev`, …) werden übersprungen.
- GPU-Passthrough (DeviceRequests) wird übernommen, sofern der Treiber auf dem Host verfügbar ist.
- Der Restore überschreibt vorhandene Daten an den Originalpfaden – deshalb gibt es in der UI eine Bestätigung.
- Sicherheits-Hinweis: Der Container erhält den Docker-Socket und damit volle Kontrolle über den Host. Nur im vertrauenswürdigen LAN betreiben.
