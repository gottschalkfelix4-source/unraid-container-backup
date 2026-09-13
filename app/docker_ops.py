"""Docker-Operationen: Inspection, Template-Suche, Image-Pull, Recreate."""
import os
import xml.etree.ElementTree as ET

import docker
from docker.errors import ImageNotFound, NotFound

# Felder aus dem Inspect-HostConfig, die die Create-API nicht akzeptiert
SKIP_HOST_CONFIG_KEYS = {"Mounts", "Binds", "ConsoleSize"}


def get_client(docker_host):
    return docker.DockerClient(base_url=docker_host, version="auto")


def find_template(templates_dir, container_name):
    """Findet das Unraid-Template (XML) zu einem Container-Namen.

    templates_dir: Pfad oder Liste von Pfaden.
    """
    dirs = templates_dir if isinstance(templates_dir, (list, tuple)) else [templates_dir]
    for tdir in dirs:
        if not os.path.isdir(tdir):
            continue
        found = _find_in_dir(tdir, container_name)
        if found:
            return found
    return None


def _find_in_dir(templates_dir, container_name):
    for fn in sorted(os.listdir(templates_dir)):
        if not fn.lower().endswith(".xml"):
            continue
        path = os.path.join(templates_dir, fn)
        tpl_name = fn[:-4]
        try:
            root = ET.parse(path).getroot()
            el = root.find(".//Name")
            if el is not None and el.text:
                tpl_name = el.text.strip()
        except ET.ParseError:
            pass
        if tpl_name.lower() == container_name.lower():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return {"filename": fn, "content": f.read()}
    return None


def ensure_image(api, image_ref, log=print):
    """Stellt sicher, dass das Image lokal vorhanden ist (sonst Pull)."""
    if not image_ref:
        return False
    try:
        api.inspect_image(image_ref)
        log(f"Image '{image_ref}' ist lokal vorhanden")
        return False
    except ImageNotFound:
        pass
    log(f"Ziehe Image '{image_ref}' ...")
    for chunk in api.pull(image_ref, stream=True, decode=True):
        status = chunk.get("status") or chunk.get("error")
        if status:
            log(f"  {status}")
    return True


def ensure_network(api, name, inspect_dict):
    """Stellt sicher, dass ein benutzerdefiniertes Netzwerk existiert.

    Fehlt es (z. B. auf einem frischen Server), wird es anhand der Daten aus
    dem Inspect-JSON neu angelegt (Treiber, Optionen, Labels).
    """
    if name in ("bridge", "host", "none", "default"):
        return None
    try:
        return api.inspect_network(name)
    except NotFound:
        pass
    nets = ((inspect_dict.get("NetworkSettings") or {}).get("Networks") or {})
    info = nets.get(name) or {}
    attrs = {"name": name, "driver": info.get("Driver") or "bridge"}
    if info.get("Options"):
        attrs["options"] = info["Options"]
    if info.get("Labels"):
        attrs["labels"] = info["Labels"]
    return api.create_network(**attrs)


def _container_networks(ins):
    """Alle Netzwerknamen, denen der Container angehört (inspect-basiert)."""
    settings = ((ins.get("NetworkSettings") or {}).get("Networks") or {})
    configured = ((ins.get("NetworkingConfig") or {}).get("Networks") or {})
    names = list(settings) or list(configured)
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def recreate_container(api, name, ins, log=print):
    """Legt einen Container exakt so an, wie er im gesicherten Inspect-JSON steht."""
    cfg = ins.get("Config") or {}
    hc_src = ins.get("HostConfig") or {}
    hc = {
        k: v
        for k, v in hc_src.items()
        if k not in SKIP_HOST_CONFIG_KEYS and v not in (None, "", [], {})
    }

    # Mounts als API-Dicts in den HostConfig (Schema: Target/Source/Type/...)
    mounts_api = []
    for m in ins.get("Mounts") or []:
        mtype = m.get("Type")
        if mtype == "bind":
            mounts_api.append(
                {
                    "Target": m["Destination"],
                    "Source": m["Source"],
                    "Type": "bind",
                    "ReadOnly": not m.get("RW", True),
                    "Propagation": m.get("Propagation") or "rprivate",
                }
            )
        elif mtype == "volume":
            mounts_api.append(
                {
                    "Target": m["Destination"],
                    "Source": m.get("Name") or m.get("Source"),
                    "Type": "volume",
                    "ReadOnly": not m.get("RW", True),
                }
            )
        elif mtype == "tmpfs":
            mounts_api.append({"Target": m["Destination"], "Type": "tmpfs"})
    hc["Mounts"] = mounts_api

    # Port-Bindings kommen im Inspect unter NetworkSettings.Ports
    port_bindings = {}
    for cport, binds in ((ins.get("NetworkSettings") or {}).get("Ports") or {}).items():
        if binds:
            port_bindings[cport.split("/")[0]] = [
                {"HostIp": b.get("HostIp") or "", "HostPort": b.get("HostPort") or ""}
                for b in binds
            ]
    hc["PortBindings"] = port_bindings

    settings_nets = ((ins.get("NetworkSettings") or {}).get("Networks") or {})
    net_aliases = {}
    for net_name, nw in settings_nets.items():
        if net_name in ("bridge", "host", "none"):
            continue
        aliases = nw.get("Aliases") or []
        if aliases:
            net_aliases[net_name] = aliases

    endpoints = {net: {} for net in _container_networks(ins)}
    networking_config = {"EndpointsConfig": endpoints} if endpoints else None

    container = api.create_container(
        image=cfg.get("Image"),
        name=name,
        command=cfg.get("Cmd"),
        entrypoint=cfg.get("Entrypoint"),
        hostname=cfg.get("Hostname"),
        domainname=cfg.get("Domainname"),
        user=cfg.get("User"),
        environment=cfg.get("Env"),
        working_dir=cfg.get("WorkingDir"),
        labels=cfg.get("Labels"),
        host_config=hc,
        networking_config=networking_config,
        healthcheck=cfg.get("Healthcheck"),
        tty=cfg.get("Tty"),
        stdin_open=cfg.get("OpenStdin"),
        mac_address=cfg.get("MacAddress"),
        stop_signal=cfg.get("StopSignal"),
        stop_timeout=cfg.get("StopTimeout"),
    )
    log(f"Container '{name}' angelegt")
    container_id = container["Id"] if isinstance(container, dict) else container.id

    net_mode = hc_src.get("NetworkMode") or "default"
    if net_mode in ("default", "bridge"):
        for net_name in endpoints:
            if net_name in ("bridge", "host", "none"):
                continue
            ensure_network(api, net_name, ins)
            api.connect_container_to_network(
                container_id, net_name, aliases=net_aliases.get(net_name) or None
            )
            if net_name in net_aliases:
                log(f"Mit Netzwerk '{net_name}' verbunden (Aliase: {', '.join(net_aliases[net_name])})")
            else:
                log(f"Mit Netzwerk '{net_name}' verbunden")

    api.start(container_id)
    return container
