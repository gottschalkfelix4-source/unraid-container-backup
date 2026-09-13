<p align="center">
  <img src="template/icon.png" width="140" alt="DockGuard Logo">
</p>

<h1 align="center">DockGuard</h1>

<p align="center">
  <b>Sichere deine Unraid-Docker-Container auf einen anderen Server – und stelle sie mit einem Klick wieder her.</b><br>
  <i>Daten an den Originalpfaden, Unraid-Template und exakte Container-Konfiguration – als wäre nichts passiert.</i>
</p>

---

DockGuard ist ein Backup-Tool als Docker-Container für Unraid. Es sichert jeden Container auf einen anderen Server – per **SMB-Share** oder **S3-Storage** – und stellt ihn nach einem kompletten Löschen **mit einem Klick wieder her**:

- **Daten** an den Originalpfaden (Bind-Mounts & benannte Volumes)
- **Unraid-Template** (Community Apps) aus `templates-user` – der Container erscheint danach wieder in der Docker-UI
- **Container** selbst: Image, Ports, Environment, Netzwerk, Restart-Policy, Labels – exakt wie vorher

<div align="center">
  <img src="template/icon.png" width="140" alt="DockGuard Logo">
</div>

## Features

| | |
|---|---|
| 🗂️ **SMB oder S3** | Ziel per rclone: NAS/Samba-Shares oder S3-kompatible Speicher (AWS, Minio, Garage, Wasabi, …) |
| ⏱️ **Cron-Planung** | Automatische Backups im UTC-Zeitplan, inkl. Aufbewahrung (Retention) |
| ⚡ **Parallele Backups** | Mehrere Container gleichzeitig sichern (1–16 Worker) |
| 🔒 **Atomare Uploads** | Backups werden erst als `.part` hochgeladen, dann umbenannt – und optional per Größencheck verifiziert. Halbfertige Uploads erscheinen nie als gültiges Backup |
| 🔐 **Secret-Masking** | Passwörter & Access-Keys werden in der Web-UI nie im Klartext angezeigt |
| 🌐 **Restore auf frischem Server** | Fehlende Docker-Netzwerke (inkl. Aliase) werden automatisch rekonstruiert |
| 🖥️ **Komplette Web-UI** | Dashboard, Container-Karten, Backup-Verwaltung, Einstellungs-Assistent, Live-Job-Logs – ohne externe Abhängigkeiten |
| 🧪 **Umfangreich getestet** | 57 automatische Tests für Backup-/Restore-Logik, Sicherheit und UI |

## Funktionsweise

Jedes Backup eines Containers ist eine einzelne `.tar`-Datei im Speicherziel:

```
<ziel>/<container-name>/
└── <container-name>_20260913_030000.tar
    ├── meta.json        # vollständiges docker inspect + Mount-Liste + Template
    └── volumes/
        ├── mnt_user_appdata_jellyfin.tar.gz
        └── ...
```

**Restore-Ablauf (ein Klick in der Web-UI):**
1. Neuestes (oder gewähltes) Backup wird heruntergeladen
2. Daten werden an die Originalpfade extrahiert (mit Traversal-Schutz)
3. Unraid-Template wird wiederhergestellt (`templates-user`)
4. Image wird bei Bedarf neu gezogen, fehlende Netzwerke werden rekonstruiert
5. Container wird exakt wie vorher neu angelegt und gestartet

## Installation

### 1. Image bauen

Auf dem Unraid-Host (oder einem anderen Docker-Host):

```bash
./build.sh
# oder: docker build -t dockguard:latest .
```

### 2. Unraid-Template (Community Apps) – Minimal-Setup

Das fertige Template liegt unter [`template/dockguard.xml`](template/dockguard.xml). Es ist bewusst **minimal gehalten** – nur zwei Einstellungen sind sichtbar:

| Einstellung | Wert |
|---|---|
| **Web-UI Port** | `8080` |
| **AppData-Verzeichnis** | `/mnt/user/appdata/dockguard` |

Alles Weitere – Speicher-Ziel (SMB/S3), Zugangsdaten, Zeitplan, Aufbewahrung, Ausschlüsse, Login – wird **in der Web-UI** konfiguriert und in `/config/settings.json` gespeichert.

**Setup in 3 Minuten:**
1. `template/dockguard.xml` nach `/boot/config/plugins/dockerMan/templates-user/` kopieren
2. In der Docker-UI („Add Container“) die Vorlage **dockguard** wählen – Port und AppData sind bereits passend vorbelegt, Container starten
3. Web-UI unter `http://<unraid-ip>:8080` öffnen und im Dashboard auf **„Jetzt einrichten“** klicken – Ziel eintragen, **„Verbindung testen“**, speichern, fertig

> **Advanced-Ansicht:** In der Template-Advanced-Ansicht liegen die (mit passenden
> Defaults vorbelegten) Pfade für Docker-Socket, `/mnt/user`, `/boot/config` und
> `/var/lib/docker/volumes` – sie sind für Backup/Restore erforderlich und müssen
> normalerweise nicht angefasst werden.

> **Kein Host-Setup nötig:** Das Speicher-Ziel wird **direkt aus dem Container
> heraus** erreicht – DockGuard spricht SMB und S3 selbst über rclone. Es gibt
> keinen SMB-Mount, keine Credentials oder rclone-Einrichtung auf dem
> Unraid-Host.

**Option B – Manuell (Docker-CLI / Compose):**

| Feld | Wert |
|---|---|
| Repository | `dockguard:latest` |
| Container-Name | `dockguard` |
| Host-Port | `8080` → Container-Port `8080` |
| Volume `/config` | `/mnt/user/appdata/dockguard` |
| Volume `/var/run/docker.sock` | `/var/run/docker.sock` (ro) |
| Volume `/mnt/user` | `/mnt/user` |
| Volume `/boot/config` | `/boot/config` |
| Volume `/var/lib/docker/volumes` | `/var/lib/docker/volumes` |

Oder direkt [`unraid-compose.yml`](unraid-compose.yml) verwenden.

> **Wichtig:** Das Tool braucht Zugriff auf den Docker-Socket (= Root-Rechte auf dem Host) sowie Schreibzugriff auf `/mnt/user`, `/boot/config` und `/var/lib/docker/volumes` für den Restore.

### 3. Konfiguration

**Primär über die Web-UI** (wird in `/config/settings.json` gespeichert und überschreibt die Umgebungsvariablen). Die Env-Variablen unten sind optional und dienen nur als Defaults für den Erststart – im Minimal-Setup wird keine einzige gesetzt, der Container startet unkonfiguriert und alles wird über die UI eingegeben.

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
| `EXCLUDE_CONTAINERS` | `dockguard,container-backup` | Kommagetrennte Namen, die nicht gesichert werden |
| `EXCLUDE_MOUNTS` | `""` | Kommagetrennte Ausdrücke; Mounts, deren Quelle oder Ziel sie enthalten, werden übersprungen (z. B. Caches) |
| `TEMPLATES_DIR` | `/boot/config/plugins/dockerMan/templates-user` | Pfad(e) zu den Unraid-Templates, mehrere kommagetrennt möglich (Restore schreibt ins erste) |
| `RCLONE_TIMEOUT` | `7200` | Timeout pro rclone-Befehl in Sekunden |
| `RCLONE_BWLIMIT` | `""` | Upload-Bandbreitenlimit (rclone-Syntax, z. B. `8M`, `500K`), leer = unbegrenzt |
| `VERIFY_UPLOAD` | `1` | Größencheck des Backups auf dem Ziel nach dem Upload (`0` = aus) |
| `STAGING_DIR` | `/staging` | Temporäre Ablage während Backup/Restore |
| `WEB_PORT` | `8080` | Port der Web-UI |
| `WEB_USER` / `WEB_PASSWORD` | – | Optionaler Login für die Web-UI (Basicauth) |

> **Passwörter:** SMB-Passwort und S3-Secret-Key werden in der Web-UI nur
> maskiert angezeigt (`********`) und nie im Klartext an den Browser
> zurückgegeben. Ein gespeicherter Wert bleibt beim Speichern erhalten,
> solange die Maske unverändert zurückgeschickt wird.

## Web-UI

Nach dem Start unter `http://<unraid-ip>:8080` (optional mit Login, wenn `WEB_PASSWORD` gesetzt ist):

- **Übersicht** – Dashboard mit Kennzahlen (Container, Backups, Speicherbelegung, nächster geplanter Lauf, letztes Backup) und den letzten Aufgaben
- **Container** – alle Container als Karten mit Status, Image, Backup-Zähler; Buttons „Sichern“ bzw. „Wiederherstellen“, Suche und Filter (Alle / Läuft / Gestoppt)
- **Backups** – alle Backups gruppiert nach Container mit Suche; pro Backup „Wiederherstellen“ (ein Klick) oder Löschen
- **Einstellungen** – Ziel (SMB/S3 inkl. Zugangsdaten), Zeitplan mit Presets, Aufbewahrung, parallele Backups, Bandbreitenlimit, Ausschlüsse (Container & Mounts), Template-Pfade, Timeout. Mit **„Verbindung testen“** lässt sich das Ziel vor dem Speichern prüfen; gespeichert wird in `/config/settings.json`
- **Aufgaben** – Live-Log aller laufenden/letzten Jobs mit Filter, Auto-Scroll und aufklappbaren Logs

> **Priorität:** Werte aus der Web-UI (settings.json) überschreiben die Umgebungsvariablen. Die Env-Vars dienen als Defaults beim ersten Start – der Container startet auch ohne Konfiguration, dann einfach alles über die UI eintragen.

## CLI

```bash
docker exec -it dockguard python -m app.cli status
docker exec -it dockguard python -m app.cli list
docker exec -it dockguard python -m app.cli backup --all
docker exec -it dockguard python -m app.cli backup --container jellyfin
docker exec -it dockguard python -m app.cli restore jellyfin            # neuestes Backup
docker exec -it dockguard python -m app.cli restore jellyfin --overwrite # existierenden ersetzen
docker exec -it dockguard python -m app.cli delete jellyfin jellyfin_20260901_030000.tar
```

## Entwicklung & Tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python tests/test_smoke.py        # 57 Tests: Backend-Logik, Sicherheit, Restore
```

## Grenzen & Hinweise

- Gesichert werden **Dateien der Mounts**, die Konfiguration und das Template. Das **Image** wird beim Restore bei Bedarf neu gezogen (nicht als Layer gesichert).
- tmpfs-Mounts und Systempfade (`/etc`, `/dev`, …) werden übersprungen; weitere Mounts lassen sich über `EXCLUDE_MOUNTS` ausschließen.
- GPU-Passthrough (DeviceRequests) wird übernommen, sofern der Treiber auf dem Host verfügbar ist.
- Der Restore überschreibt vorhandene Daten an den Originalpfaden – deshalb gibt es in der UI eine Bestätigung.
- Sicherheits-Hinweis: Der Container erhält den Docker-Socket und damit volle Kontrolle über den Host. Nur im vertrauenswürdigen LAN betreiben (optional per `WEB_PASSWORD` absichern).
