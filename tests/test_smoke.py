"""Smoke-Test: Backup/Restore-Logik mit simulierten Docker- und Storage-Objekten."""
import datetime as dt
import json
import os
import shutil
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.storage import Storage
from app import backup as backup_mod
from app import restore as restore_mod
from app.docker_ops import find_template

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("  OK  " if cond else " FAIL ") + name)


# ---------- 1. Config ----------
print("== Config ==")
os.environ.update({
    "BACKUP_TYPE": "smb", "SMB_HOST": "192.168.1.10", "SMB_USER": "u",
    "SMB_PASS": "p", "SMB_SHARE": "backup", "SMB_PATH": "unraid",
})
cfg = Config()
cfg.validate()
check("smb validiert", True)
check("remote_base smb", cfg.remote_base == "backup:backup/unraid")

os.environ.update({"BACKUP_TYPE": "s3", "S3_BUCKET": "bkt", "S3_ACCESS_KEY": "ak",
                   "S3_SECRET_KEY": "sk", "S3_PATH": "deep/path"})
cfg3 = Config()
cfg3.validate()
check("remote_base s3", cfg3.remote_base == "backup:bkt/deep/path")

os.environ.update({"BACKUP_TYPE": "smb", "SMB_HOST": ""})
try:
    Config().validate()
    check("fehlende Var wird abgelehnt", False)
except ValueError:
    check("fehlende Var wird abgelehnt", True)
os.environ["SMB_HOST"] = "192.168.1.10"

# ---------- 2. Storage rclone-Konfig ----------
print("== Storage rclone.conf ==")
root = tempfile.mkdtemp(prefix="cbtest_")
cfg.rclone_conf = os.path.join(root, "conf", "rclone.conf")
st = Storage(cfg)
content = open(cfg.rclone_conf).read()
check("smb remote konfiguriert", "type = smb" in content and "host = 192.168.1.10" in content and "share_name = backup" in content)

cfg3.rclone_conf = os.path.join(root, "conf", "rclone3.conf")
Storage(cfg3)
content3 = open(cfg3.rclone_conf).read()
check("s3 remote konfiguriert", "type = s3" in content3 and "access_key_id = ak" in content3)


# ---------- 3. Fake-Docker-Client + Fake-Storage ----------
class FakeApi:
    def __init__(self, ins):
        self.ins = ins
        self.created = None
        self.started = None
        self.live = set()  # Namen existierender Container

    def inspect_container(self, name):
        if name not in self.live:
            from docker.errors import NotFound
            raise NotFound(f"no such container: {name}")
        return self.ins

    def inspect_image(self, ref):
        return {"Id": "img123"}

    def create_container(self, **kw):
        self.created = kw
        self.live.add(kw["name"])
        return {"Id": "fakeid123"}

    def start(self, cid):
        self.started = cid


class FakeContainer:
    def __init__(self, name):
        self.name = name


class FakeClient:
    def __init__(self, ins):
        self.api = FakeApi(ins)

    def containers_list(self, all=True):
        return [FakeContainer(self.ins["Name"].lstrip("/"))]


class LocalStorage:
    """Simuliert rclone mit einem lokalen Verzeichnis."""
    def __init__(self, base):
        self.base = base
        os.makedirs(base, exist_ok=True)

    def upload(self, local_file, rel_dir):
        dest = os.path.join(self.base, rel_dir) if rel_dir else self.base
        os.makedirs(dest, exist_ok=True)
        shutil.copy(local_file, dest)

    def download(self, rel_path, local_dir):
        src = os.path.join(self.base, rel_path)
        os.makedirs(local_dir, exist_ok=True)
        shutil.copy(src, local_dir)

    def list_containers(self):
        return sorted(d for d in os.listdir(self.base) if os.path.isdir(os.path.join(self.base, d)))

    def list_backups(self, container):
        d = os.path.join(self.base, container)
        if not os.path.isdir(d):
            return []
        return [{"id": f, "size": os.path.getsize(os.path.join(d, f)), "mtime": ""}
                for f in sorted(os.listdir(d)) if f.endswith(".tar")]

    def delete_backup(self, container, backup_id):
        os.remove(os.path.join(self.base, container, backup_id))


# ---------- 4. Testdaten: realistische Inspect-Struktur ----------
print("== Testdaten ==")
data_root = os.path.join(root, "mnt_user_appdata_jellyfin")
os.makedirs(os.path.join(data_root, "metadata"), exist_ok=True)
with open(os.path.join(data_root, "config.xml"), "w") as f:
    f.write("<media-server><name>Jellyfin</name></media-server>")
with open(os.path.join(data_root, "metadata", "db.sqlite"), "wb") as f:
    f.write(b"\x00\x01binary" * 100)
os.symlink("/etc/hostname", os.path.join(data_root, "link"))

tpl_dir = os.path.join(root, "templates-user")
os.makedirs(tpl_dir)
with open(os.path.join(tpl_dir, "Jellyfin.xml"), "w") as f:
    f.write('<?xml version="1.0"?><TemplateUI><Config><Name>Jellyfin</Name>'
            '<Image>jellyfin/jellyfin:latest</Image></Config></TemplateUI>')

INSPECT = {
    "Id": "abc123",
    "Name": "/jellyfin",
    "Config": {
        "Image": "jellyfin/jellyfin:latest",
        "Env": ["UID=1000", "GID=1000", "TZ=Europe/Berlin"],
        "Cmd": None,
        "Entrypoint": None,
        "Hostname": "jellyfin",
        "Domainname": None,
        "User": "",
        "WorkingDir": "/",
        "Labels": {"io.unraid.label.managedby": "LinuxServer.io"},
        "Tty": False,
        "OpenStdin": False,
        "StopSignal": "SIGTERM",
        "StopTimeout": 10,
        "Healthcheck": None,
        "MacAddress": None,
        "ExposedPorts": {"8096/tcp": {}},
    },
    "HostConfig": {
        "NetworkMode": "bridge",
        "PortBindings": None,
        "RestartPolicy": {"Name": "always", "MaximumRetryCount": 0},
        "Memory": 0,
        "CpuShares": 0,
        "Privileged": False,
        "IpcMode": "private",
        "PidMode": "",
        "UtsMode": "",
        "UsernsMode": "",
        "Runtime": "runc",
        "ConsoleSize": [0, 0],
        "Mounts": [{"Source": data_root, "Destination": "/config", "Type": "bind", "RW": True}],
        "OomScoreAdj": 0,
        "CgroupnsMode": "private",
    },
    "Mounts": [
        {"Type": "bind", "Source": data_root, "Destination": "/config", "RW": True, "Propagation": "rprivate"},
        {"Type": "bind", "Source": "/etc/resolv.conf", "Destination": "/etc/resolv.conf", "RW": False, "Propagation": ""},
        {"Type": "tmpfs", "Source": "tmpfs", "Destination": "/tmp", "RW": True},
    ],
    "NetworkSettings": {
        "Ports": {"8096/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8096"}]},
    },
    "NetworkingConfig": {"Networks": {"bridge": {"IPAMConfig": None}}},
    "State": {"Status": "running"},
}

check("Template gefunden", (find_template(tpl_dir, "jellyfin") or {}).get("filename") == "Jellyfin.xml")
check("Template nicht gefunden (falscher Name)", find_template(tpl_dir, "plex") is None)
check("Template in Verzeichnisliste gefunden", (find_template(["/nonexistent", tpl_dir], "jellyfin") or {}).get("filename") == "Jellyfin.xml")

# ---------- 5. Backup ----------
print("== Backup ==")
cfg.staging_dir = os.path.join(root, "staging")
os.makedirs(cfg.staging_dir, exist_ok=True)
cfg.templates_dir = tpl_dir
cfg.templates_dirs = [tpl_dir]
cfg.keep = 0

storage = LocalStorage(os.path.join(root, "remote"))
client = FakeClient(INSPECT)
client.api.live = {"jellyfin"}  # Container existiert vor dem Backup
logs = []
result = backup_mod.backup_container(client, cfg, storage, "jellyfin", log=logs.append)
check("Backup-Ergebnis enthält Container", result["container"] == "jellyfin")
check("Archive erstellt", result["archive"].startswith("jellyfin_") and result["archive"].endswith(".tar"))
check("Nur Jellyfin-Mount gesichert (resolv.conf/tmpfs übersprungen)", len(result["mounts"]) == 1)
check("Mount-Quelle korrekt", result["mounts"][0]["source"] == data_root)

backups = storage.list_backups("jellyfin")
check("Backup im 'Remote' vorhanden", len(backups) == 1)

# Archiv-Inhalt prüfen
import io
arc = os.path.join(root, "remote", "jellyfin", backups[0]["id"])
with tarfile.open(arc) as tf:
    names = tf.getnames()
    meta = json.loads(tf.extractfile("meta.json").read())
    vol_files = [n for n in names if n.startswith("volumes/") and n.endswith(".tar.gz")]
    vol_bytes = tf.extractfile(vol_files[0]).read() if vol_files else b""
check("meta.json im Archiv", meta["container"] == "jellyfin")
check("Template in meta", meta["template"]["filename"] == "Jellyfin.xml")
check("Inspect in meta", meta["inspect"]["Config"]["Image"] == "jellyfin/jellyfin:latest")
check("Volume-Tar im Archiv", len(vol_files) == 1)
with tarfile.open(fileobj=io.BytesIO(vol_bytes), mode="r:gz") as vtf:
    vnames = vtf.getnames()
check("Dateien im Volume-Tar", "config.xml" in vnames and "metadata/db.sqlite" in vnames)

# ---------- 6. Restore ----------
print("== Restore ==")
# Daten löschen + Container "komplett gelöscht", um Restore zu simulieren
shutil.rmtree(data_root)
tpl_file = os.path.join(tpl_dir, "Jellyfin.xml")
os.remove(tpl_file)
client.api.live = set()

logs2 = []
res = restore_mod.restore(storage, cfg, client, "jellyfin", overwrite=False, log=logs2.append)
check("Restore-Ergebnis", res["container"] == "jellyfin" and res["template_restored"] is True)
check("Daten wiederhergestellt", os.path.exists(os.path.join(data_root, "config.xml")))
check("Binärdatei intakt", open(os.path.join(data_root, "metadata", "db.sqlite"), "rb").read() == b"\x00\x01binary" * 100)
check("Symlink erhalten", os.path.islink(os.path.join(data_root, "link")))
check("Template wiederhergestellt", os.path.exists(tpl_file))
check("Container neu angelegt", client.api.created is not None)
check("Container gestartet", client.api.started == "fakeid123")

created = client.api.created
check("Image übernommen", created["image"] == "jellyfin/jellyfin:latest")
check("Name übernommen", created["name"] == "jellyfin")
check("Env übernommen", created["environment"] == ["UID=1000", "GID=1000", "TZ=Europe/Berlin"])
hc = created["host_config"]
check("RestartPolicy übernommen", hc["RestartPolicy"] == {"Name": "always", "MaximumRetryCount": 0})
check("PortBinding übernommen", hc["PortBindings"] == {"8096": [{"HostIp": "0.0.0.0", "HostPort": "8096"}]})
check("Bind-Mount übernommen", any(m["Source"] == data_root and m["Target"] == "/config" and m["Type"] == "bind" for m in hc["Mounts"]))
check("tmpfs-Mount übernommen", any(m["Type"] == "tmpfs" for m in hc["Mounts"]))
check("resolv.conf-Mount übersprungen? nein - war im Inspect", any(m["Source"] == "/etc/resolv.conf" for m in hc["Mounts"]))
check("Kein Mounts-Feld aus Inspect-HostConfig doppelt", hc.get("Mounts") is not None)

# Restore mit bestehendem Container ohne overwrite -> Fehler
try:
    restore_mod.restore(storage, cfg, client, "jellyfin", overwrite=False, log=lambda m: None)
    check("Bestehender Container blockiert Restore", False)
except Exception as e:
    check("Bestehender Container blockiert Restore", "existiert bereits" in str(e))

# ---------- 7. Retention ----------
print("== Retention ==")
for i in range(5):
    f = os.path.join(root, "remote", "jellyfin", f"jellyfin_2026010{i+1}_000000.tar")
    with open(f, "w") as fh:
        fh.write("x")
cfg.keep = 3
backup_mod._apply_retention(storage, "jellyfin", cfg.keep, lambda m: None)
remaining = storage.list_backups("jellyfin")
check("Retention hält 3 Backups", len(remaining) == 3)
check("Älteste gelöscht", not any(b["id"].startswith("jellyfin_20260101") or b["id"].startswith("jellyfin_20260102") for b in remaining))

# ---------- 8. Neues: Secret-Masking, bwlimit, exclude_mounts ----------
print("== Neue Config-Funktionen ==")
os.environ.update({"BACKUP_TYPE": "smb", "SMB_HOST": "h", "SMB_USER": "u",
                   "SMB_PASS": "p", "SMB_SHARE": "s"})
cfgx = Config()
cfgx.validate()
check("Passwort maskiert in to_dict", cfgx.to_dict()["smb_pass"] == "********")
check("Intern bleibt Passwort erhalten", cfgx.smb_pass == "p")
cfgx.update({"smb_pass": "********", "backup_type": "smb"})
check("Maske beim Speichern erhält Wert", cfgx.smb_pass == "p")
cfgx.update({"smb_pass": ""})
check("Leeren String löscht Passwort", cfgx.smb_pass == "")
cfgx.smb_pass = "p"
try:
    cfgx.bwlimit = "100GBG"
    cfgx._parse()
    cfgx.validate()
    check("bwlimit ungültig abgelehnt", False)
except ValueError:
    check("bwlimit ungültig abgelehnt", True)
cfgx.bwlimit = "8M"
cfgx._parse()
cfgx.validate()
check("bwlimit gültig akzeptiert", True)
check("exclude parsed", cfgx.exclude == ["dockguard", "container-backup"])
cfgx.exclude_mounts = ["/cache", "plex/Library"]
check("exclude_mounts parsed", cfgx.exclude_mounts == ["/cache", "plex/Library"])

print("== exclude_mounts beim Backup ==")
ins2 = json.loads(json.dumps(INSPECT))
cache_dir = os.path.join(root, "appdata_cache")
os.makedirs(cache_dir)
with open(os.path.join(cache_dir, "cache.bin"), "wb") as f:
    f.write(b"c" * 512)
ins2["Mounts"].append({"Type": "bind", "Source": cache_dir, "Destination": "/config/cache", "RW": True})
cfg.exclude_mounts = ["/config/cache"]
client8 = FakeClient(ins2)
client8.api.live = {"jellyfin"}
res2 = backup_mod.backup_container(client8, cfg, storage, "jellyfin", log=lambda m: None)
check("exclude_mounts übersprungen", len(res2["mounts"]) == 1 and res2["mounts"][0]["source"] == data_root)
cfg.exclude_mounts = []


# ---------- 9. Neues: sicheres Entpacken (Traversal-Schutz) ----------
print("== Safe-Extract ==")
import io as _io
evil = os.path.join(root, "evil.tar")
with tarfile.open(evil, "w") as tf:
    entries = {
        "meta.json": b'{"container": "x"}',
        "volumes/ok.tar.gz": b"DATA",
        "../evil.txt": b"BOOM",
        "README.md": b"FREMDE",
        "/abs/pwned": b"NOPE",
    }
    for name, data in entries.items():
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        with _io.BytesIO(data) as bio:
            tf.addfile(ti, bio)
extract_dir = os.path.join(root, "extract_dir")
os.makedirs(extract_dir, exist_ok=True)
with tarfile.open(evil, "r") as tf:
    restore_mod._safe_extract(tf, extract_dir, lambda m: None)
check("Erlaubte Dateien extrahiert",
      os.path.isfile(os.path.join(extract_dir, "meta.json"))
      and os.path.isfile(os.path.join(extract_dir, "volumes", "ok.tar.gz")))
check("Traversal blockiert (../evil.txt)", not os.path.exists(os.path.join(root, "evil.txt")))
check("Absoluter Pfad blockiert", not os.path.exists("/abs/pwned"))
check("Fremde Elemente übersprungen", not os.path.exists(os.path.join(extract_dir, "README.md")))


# ---------- 10. Neues: Restore mit benutzerdefiniertem Netzwerk ----------
print("== Restore mit Custom-Netzwerk ==")


class FakeApiNet(FakeApi):
    def __init__(self, ins):
        super().__init__(ins)
        self.networks = {}
        self.created_net = None
        self.connected = []

    def inspect_network(self, name):
        if name not in self.networks:
            from docker.errors import NotFound
            raise NotFound(f"network {name} not found")
        return {"Name": name, "Id": "net-" + name}

    def create_network(self, **kw):
        self.created_net = kw
        self.networks[kw["name"]] = kw
        return {"Id": "net123", "Name": kw["name"]}

    def connect_container_to_network(self, cid, net, aliases=None):
        self.connected.append((cid, net, aliases))


class FakeClientNet(FakeClient):
    def __init__(self, ins):
        self.api = FakeApiNet(ins)


ins3 = json.loads(json.dumps(INSPECT))
ins3["Name"] = "/mynet-app"
ins3["Config"]["Image"] = "nginxdemos/hello:latest"
ins3["NetworkSettings"]["Networks"] = {
    "mynet": {"Aliases": ["app1"], "Driver": "bridge",
              "Options": {"com.docker.network.bridge.enable_icc": "true"}},
}
client3 = FakeClientNet(ins3)
client3.api.live = {"mynet-app"}
st3 = LocalStorage(os.path.join(root, "remote3"))
backup_mod.backup_container(client3, cfg, st3, "mynet-app", log=lambda m: None)
client3.api.live = set()
res3 = restore_mod.restore(st3, cfg, client3, "mynet-app", log=lambda m: None)
check("Custom-Network recreated", (client3.api.created_net or {}).get("name") == "mynet")
check("Network-Alias übergeben",
      any(net == "mynet" and aliases == ["app1"] for _, net, aliases in client3.api.connected))
check("Container verbunden", len(client3.api.connected) == 1)


# ---------- 11. Neues: Backup-Zeitstempel (Backend) ----------
print("== Zeitstempel-Parsing ==")
from app.main import _backup_epoch

expected = dt.datetime(2026, 9, 13, 3, 0, 0, tzinfo=dt.timezone.utc).timestamp()
check("Epoch aus Backup-ID",
      _backup_epoch({"id": "jellyfin_20260913_030000.tar", "mtime": ""}) == expected)
expected2 = dt.datetime(2026, 9, 13, 5, 0, 0, tzinfo=dt.timezone.utc).timestamp()
check("mtime bevorzugt",
      _backup_epoch({"id": "jellyfin_20260913_030000.tar", "mtime": "2026-09-13T05:00:00Z"}) == expected2)
check("Unparsbar -> 0", _backup_epoch({"id": "jellyfin_broken", "mtime": ""}) == 0)

# ---------- Zusammenfassung ----------
failed = [n for n, ok in PASS if not ok]
print(f"\n{len(PASS) - len(failed)}/{len(PASS)} Tests bestanden")
if failed:
    print("FEHLGESCHLAGEN:", failed)
    sys.exit(1)
shutil.rmtree(root, ignore_errors=True)
