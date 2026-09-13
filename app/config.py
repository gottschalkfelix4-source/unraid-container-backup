"""Konfiguration: Umgebungsvariablen als Defaults, Web-UI-Werte in settings.json.

- Werte aus SETTINGS_FILE (Standard /config/settings.json) überschreiben die
  Umgebungsvariablen und sind über die Web-UI änderbar.
- Geheimnisse (Passwörter/Secret-Keys) werden in to_dict() maskiert und beim
  Speichern via update() wiederverwendet, solange die Maske zurückkommt.
- App startet auch mit unvollständiger Konfiguration; strikte Validierung
  erfolgt erst beim Speichern über die Web-UI bzw. in der CLI.
"""
import json
import os
import re
import tempfile

from apscheduler.triggers.cron import CronTrigger

SECRET_MASK = "********"

_BWLIMIT_RE = re.compile(r"^(off|\d+(\.\d+)?\s*[KMGTPE]?i?[bB]?)$", re.IGNORECASE)


def _env(key, default=None):
    v = os.environ.get(key, "").strip()
    return v if v else default


def _to_int(value, default):
    s = str(value).strip()
    if not s:
        return default
    try:
        return int(s)
    except (TypeError, ValueError):
        raise ValueError(f"'{value}' ist keine gültige Zahl")


def _to_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _split_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(c).strip() for c in value if str(c).strip()]
    return [c.strip() for c in str(value).split(",") if c.strip()]


# Per Web-UI konfigurierbare Werte (in der Einstellungsdatei persistiert)
CONFIGURABLE_KEYS = [
    "backup_type",
    "smb_host", "smb_port", "smb_user", "smb_pass", "smb_share", "smb_path",
    "s3_provider", "s3_endpoint", "s3_region", "s3_bucket",
    "s3_access_key", "s3_secret_key", "s3_path",
    "schedule", "keep", "web_port", "exclude", "exclude_mounts",
    "templates_dir", "rclone_timeout", "parallel", "bwlimit", "verify_upload",
]

# Werte, die nie im Klartext an die UI zurückgegeben werden (nur als Maske)
SECRET_KEYS = ("smb_pass", "s3_secret_key")


class Config:
    def __init__(self):
        self.settings_file = _env("SETTINGS_FILE", "/config/settings.json")
        # Defaults aus Umgebungsvariablen
        self.backup_type = _env("BACKUP_TYPE", "smb")
        self.smb_host = _env("SMB_HOST", "")
        self.smb_port = _env("SMB_PORT", "445")
        self.smb_user = _env("SMB_USER", "")
        self.smb_pass = _env("SMB_PASS", "")
        self.smb_share = _env("SMB_SHARE", "")
        self.smb_path = _env("SMB_PATH", "")
        self.s3_provider = _env("S3_PROVIDER", "AWS")
        self.s3_endpoint = _env("S3_ENDPOINT", "")
        self.s3_region = _env("S3_REGION", "us-east-1")
        self.s3_bucket = _env("S3_BUCKET", "")
        self.s3_access_key = _env("S3_ACCESS_KEY", "")
        self.s3_secret_key = _env("S3_SECRET_KEY", "")
        self.s3_path = _env("S3_PATH", "")
        self.schedule = _env("BACKUP_SCHEDULE", "0 3 * * *")
        self.keep = _env("BACKUP_KEEP", "0")
        self.web_port = _env("WEB_PORT", "8080")
        self.docker_host = _env("DOCKER_HOST", "unix:///var/run/docker.sock")
        self.templates_dir = _env(
            "TEMPLATES_DIR", "/boot/config/plugins/dockerMan/templates-user"
        )
        self.staging_dir = _env("STAGING_DIR", "/staging")
        self.rclone_conf = _env("RCLONE_CONF", "/config/rclone.conf")
        self.rclone_timeout = _env("RCLONE_TIMEOUT", "7200")
        self.exclude = _env("EXCLUDE_CONTAINERS", "dockguard,container-backup")
        self.exclude_mounts = _env("EXCLUDE_MOUNTS", "")
        self.parallel = _env("BACKUP_PARALLEL", "2")
        self.bwlimit = _env("RCLONE_BWLIMIT", "")
        self.verify_upload = _env("VERIFY_UPLOAD", "1")
        # Optionaler HTTP-Basicauth (reiner Env-Schutz, nicht über UI änderbar)
        self.web_user = _env("WEB_USER", "admin")
        self.web_password = _env("WEB_PASSWORD", "")

        self._load_file()
        self._parse()

    @classmethod
    def from_dict(cls, data):
        """Aktuelle Konfiguration + Überlagerung, ohne zu speichern (z. B. zum Testen)."""
        cfg = cls()
        for k in CONFIGURABLE_KEYS:
            if k in data and data[k] is not None:
                setattr(cfg, k, data[k])
        cfg._parse()
        return cfg

    def _load_file(self):
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            for k in CONFIGURABLE_KEYS:
                if k in data and data[k] is not None:
                    setattr(self, k, data[k])

    def _parse(self):
        self.backup_type = str(self.backup_type or "smb").strip().lower()
        self.smb_port = _to_int(self.smb_port, 445)
        self.keep = _to_int(self.keep, 0)
        self.web_port = _to_int(self.web_port, 8080)
        self.rclone_timeout = _to_int(self.rclone_timeout, 7200)
        self.parallel = _to_int(self.parallel, 2)
        self.bwlimit = str(self.bwlimit or "").strip()
        self.verify_upload = _to_bool(self.verify_upload)
        self.exclude = _split_list(self.exclude)
        self.exclude_mounts = _split_list(self.exclude_mounts)
        self.templates_dirs = _split_list(self.templates_dir)

    @property
    def remote_base(self):
        if self.backup_type == "s3":
            return f"backup:{self.s3_bucket}/{self.s3_path}".rstrip("/")
        return f"backup:{self.smb_share}/{self.smb_path}".rstrip("/")

    def validate(self):
        missing = []
        if self.backup_type == "smb":
            checks = (
                ("SMB_HOST", self.smb_host),
                ("SMB_USER", self.smb_user),
                ("SMB_SHARE", self.smb_share),
            )
        elif self.backup_type == "s3":
            checks = (
                ("S3_BUCKET", self.s3_bucket),
                ("S3_ACCESS_KEY", self.s3_access_key),
                ("S3_SECRET_KEY", self.s3_secret_key),
            )
        else:
            raise ValueError(
                f"Backup-Typ muss 'smb' oder 's3' sein (ist '{self.backup_type}')"
            )
        for key, val in checks:
            if not val:
                missing.append(key)
        if missing:
            raise ValueError("Fehlende Werte: " + ", ".join(missing))
        if not 1 <= self.smb_port <= 65535:
            raise ValueError(f"SMB-Port muss zwischen 1 und 65535 liegen ({self.smb_port})")
        if not 1 <= self.web_port <= 65535:
            raise ValueError(f"Web-Port muss zwischen 1 und 65535 liegen ({self.web_port})")
        if self.keep < 0:
            raise ValueError("Backups behalten darf nicht negativ sein")
        if not 1 <= self.parallel <= 16:
            raise ValueError(f"Parallele Backups müssen zwischen 1 und 16 liegen ({self.parallel})")
        if self.rclone_timeout < 10:
            raise ValueError("rclone-Timeout muss mindestens 10 Sekunden betragen")
        if self.bwlimit and not _BWLIMIT_RE.match(self.bwlimit):
            raise ValueError(
                f"Bandbreitenlimit '{self.bwlimit}' ungültig (z. B. '8M', '500K', 'off')"
            )
        try:
            CronTrigger.from_crontab(str(self.schedule), timezone="UTC")
        except ValueError as exc:
            raise ValueError(f"Ungültiger Zeitplan '{self.schedule}': {exc}")

    def to_dict(self):
        d = {}
        for k in CONFIGURABLE_KEYS:
            v = getattr(self, k)
            if k in SECRET_KEYS and v:
                v = SECRET_MASK
            d[k] = v
        d["exclude"] = ",".join(self.exclude)
        d["exclude_mounts"] = ",".join(self.exclude_mounts)
        return d

    def update(self, data):
        """Überlagert Werte, validiert und persistiert."""
        for k in CONFIGURABLE_KEYS:
            if k not in data or data[k] is None:
                continue
            v = data[k]
            if k in SECRET_KEYS and v == SECRET_MASK:
                continue  # Maske aus der UI -> bestehenden Wert beibehalten
            setattr(self, k, v)
        self._parse()
        self.validate()
        self.save()

    def save(self):
        candidates = [
            self.settings_file,
            os.path.join(tempfile.gettempdir(), "container-backup-settings.json"),
        ]
        for path in candidates:
            try:
                conf_dir = os.path.dirname(path)
                if conf_dir:
                    os.makedirs(conf_dir, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.to_dict(), f, indent=2)
                self.settings_file = path
                return
            except OSError:
                continue
        raise OSError("Einstellungen konnten nicht gespeichert werden")
