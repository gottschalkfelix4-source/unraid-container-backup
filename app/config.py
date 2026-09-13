import json
import os
import tempfile

from apscheduler.triggers.cron import CronTrigger


def _env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


# Per Web-UI konfigurierbare Werte (werden in der Einstellungsdatei persistiert
# und überschreiben die Umgebungsvariablen)
CONFIGURABLE_KEYS = [
    "backup_type",
    "smb_host", "smb_port", "smb_user", "smb_pass", "smb_share", "smb_path",
    "s3_provider", "s3_endpoint", "s3_region", "s3_bucket",
    "s3_access_key", "s3_secret_key", "s3_path",
    "schedule", "keep", "web_port", "exclude", "templates_dir", "rclone_timeout",
]


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
        self.exclude = _env("EXCLUDE_CONTAINERS", "container-backup")

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
                if k in data:
                    setattr(self, k, data[k])

    def _parse(self):
        self.backup_type = str(self.backup_type or "smb").lower()
        self.smb_port = int(self.smb_port or 445)
        self.keep = int(self.keep or 0)
        self.web_port = int(self.web_port or 8080)
        self.rclone_timeout = int(self.rclone_timeout or 7200)
        ex = self.exclude
        if isinstance(ex, (list, tuple)):
            self.exclude = [str(c).strip() for c in ex if str(c).strip()]
        else:
            self.exclude = [c.strip() for c in str(ex).split(",") if c.strip()]
        self.templates_dirs = [
            d.strip() for d in str(self.templates_dir).split(",") if d.strip()
        ]

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
            raise ValueError(f"Backup-Typ muss 'smb' oder 's3' sein (ist '{self.backup_type}')")
        for key, val in checks:
            if not val:
                missing.append(key)
        if missing:
            raise ValueError("Fehlende Werte: " + ", ".join(missing))
        try:
            CronTrigger.from_crontab(str(self.schedule))
        except ValueError as exc:
            raise ValueError(f"Ungültiger Zeitplan '{self.schedule}': {exc}")

    def to_dict(self):
        d = {k: getattr(self, k) for k in CONFIGURABLE_KEYS}
        d["exclude"] = ",".join(self.exclude)
        return d

    def update(self, data):
        """Überlagert Werte, validiert und persistiert."""
        for k in CONFIGURABLE_KEYS:
            if k in data and data[k] is not None:
                setattr(self, k, data[k])
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
