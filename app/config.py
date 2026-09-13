import os


def _env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


class Config:
    def __init__(self):
        self.backup_type = (_env("BACKUP_TYPE", "smb") or "smb").lower()
        # SMB
        self.smb_host = _env("SMB_HOST", "")
        self.smb_port = int(_env("SMB_PORT", "445"))
        self.smb_user = _env("SMB_USER", "")
        self.smb_pass = _env("SMB_PASS", "")
        self.smb_share = _env("SMB_SHARE", "")
        self.smb_path = (_env("SMB_PATH", "") or "").strip("/")
        # S3
        self.s3_provider = _env("S3_PROVIDER", "AWS")
        self.s3_endpoint = _env("S3_ENDPOINT", "")
        self.s3_region = _env("S3_REGION", "us-east-1")
        self.s3_bucket = _env("S3_BUCKET", "")
        self.s3_access_key = _env("S3_ACCESS_KEY", "")
        self.s3_secret_key = _env("S3_SECRET_KEY", "")
        self.s3_path = (_env("S3_PATH", "") or "").strip("/")
        # Allgemein
        self.schedule = _env("BACKUP_SCHEDULE", "0 3 * * *")
        self.keep = int(_env("BACKUP_KEEP", "0") or 0)
        self.web_port = int(_env("WEB_PORT", "8080") or 8080)
        self.docker_host = _env("DOCKER_HOST", "unix:///var/run/docker.sock")
        self.templates_dir = _env(
            "TEMPLATES_DIR", "/boot/config/plugins/dockerMan/templates-user"
        )
        # Mehrere Verzeichnisse möglich (kommagetrennt); das erste wird für den Restore genutzt
        self.templates_dirs = [d.strip() for d in self.templates_dir.split(",") if d.strip()]
        self.staging_dir = _env("STAGING_DIR", "/staging")
        self.rclone_conf = _env("RCLONE_CONF", "/config/rclone.conf")
        self.rclone_timeout = int(_env("RCLONE_TIMEOUT", "7200") or 7200)
        self.exclude = [
            c.strip() for c in (_env("EXCLUDE_CONTAINERS", "container-backup") or "").split(",") if c.strip()
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
            raise ValueError(f"BACKUP_TYPE muss 'smb' oder 's3' sein (ist '{self.backup_type}')")
        for key, val in checks:
            if not val:
                missing.append(key)
        if missing:
            raise ValueError("Fehlende Umgebungsvariablen: " + ", ".join(missing))
