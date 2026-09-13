import json
import os
import subprocess
import tempfile


class StorageError(Exception):
    pass


class Storage:
    """Zugriff auf das Backup-Ziel (SMB oder S3) via rclone."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg.remote_base
        self.conf_path = self._write_rclone_conf()

    def _write_rclone_conf(self):
        cfg = self.cfg
        lines = ["[backup]"]
        if cfg.backup_type == "s3":
            lines += [
                "type = s3",
                f"provider = {cfg.s3_provider}",
                f"access_key_id = {cfg.s3_access_key}",
                f"secret_access_key = {cfg.s3_secret_key}",
                f"region = {cfg.s3_region}",
            ]
            if cfg.s3_endpoint:
                lines.append(f"endpoint = {cfg.s3_endpoint}")
        elif cfg.backup_type == "smb":
            lines += [
                "type = smb",
                f"host = {cfg.smb_host}",
                f"port = {cfg.smb_port}",
                f"user = {cfg.smb_user}",
                f"pass = {cfg.smb_pass}",
                f"share_name = {cfg.smb_share}",
            ]
        candidates = [
            cfg.rclone_conf,
            os.path.join(tempfile.gettempdir(), "container-backup-rclone.conf"),
        ]
        for conf_path in candidates:
            try:
                conf_dir = os.path.dirname(conf_path)
                if conf_dir:
                    os.makedirs(conf_dir, exist_ok=True)
                with open(conf_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                try:
                    os.chmod(conf_path, 0o600)
                except OSError:
                    pass
                return conf_path
            except OSError:
                continue
        raise StorageError("rclone-Konfiguration konnte nicht geschrieben werden")

    def _run(self, args, timeout=None, input_data=None):
        cmd = ["rclone", "--config", self.conf_path, "-q"] + args
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.cfg.rclone_timeout,
                input=input_data,
            )
        except subprocess.TimeoutExpired:
            raise StorageError(f"Zeitüberschreitung: rclone {' '.join(args)}")
        except FileNotFoundError:
            raise StorageError("rclone ist nicht installiert")
        if p.returncode != 0:
            raise StorageError(f"rclone fehlgeschlagen ({p.returncode}): {p.stderr.strip()[:500]}")
        return p.stdout

    def test(self):
        """Prüft, ob das Ziel erreichbar ist (auch wenn der Basispfad noch leer ist)."""
        for path in (self.base, self.base.rsplit("/", 1)[0]):
            try:
                self._run(["lsd", path], timeout=60)
                return True
            except StorageError:
                continue
        return False

    def upload(self, local_file, rel_dir):
        remote = f"{self.base}/{rel_dir}" if rel_dir else self.base
        self._run(
            ["copy", os.path.dirname(local_file), remote, "--include", os.path.basename(local_file)]
        )

    def download(self, rel_path, local_dir):
        os.makedirs(local_dir, exist_ok=True)
        self._run(["copy", f"{self.base}/{rel_path}", local_dir])

    def list_containers(self):
        out = self._run(["lsf", self.base, "--dirs-only"])
        return [line.strip().rstrip("/") for line in out.splitlines() if line.strip()]

    def list_backups(self, container):
        out = self._run(["lsf", f"{self.base}/{container}", "--json"])
        items = json.loads(out) if out.strip() else []
        backups = []
        for i in items:
            name = i.get("Name") or i.get("Path") or ""
            if not name.endswith(".tar"):
                continue
            backups.append(
                {
                    "id": name,
                    "size": i.get("Size", 0),
                    "mtime": i.get("ModTime", ""),
                }
            )
        backups.sort(key=lambda b: b["id"])
        return backups

    def delete_backup(self, container, backup_id):
        self._run(["deletefile", f"{self.base}/{container}/{backup_id}"])
