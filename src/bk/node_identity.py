from __future__ import annotations

import hashlib
import socket
from pathlib import Path
from typing import Optional

from .collector_status import safe_hostname


NODE_EXTENSION = "gpubk.node"


def stable_node_identity(
    *,
    machine_id: Optional[str] = None,
    hostname: Optional[str] = None,
) -> dict:
    """Return a stable, non-secret node identity suitable for persisted history."""
    host = safe_hostname(hostname if hostname is not None else socket.gethostname())
    source = _machine_id() if machine_id is None else machine_id.strip()
    seed = f"machine:{source}" if source else f"hostname:{host}"
    node_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return {"schema": 1, "id": node_id, "hostname": host}


def node_record_extension(identity: dict, *, device_uuid: str = "") -> dict:
    payload = {
        "schema": 1,
        "id": str(identity["id"]),
        "hostname": safe_hostname(str(identity.get("hostname", "unknown"))),
    }
    if device_uuid:
        payload["device_uuid"] = str(device_uuid)[:128]
    return {NODE_EXTENSION: payload}


def record_node_id(record: dict) -> str:
    extensions = record.get("extensions")
    if not isinstance(extensions, dict):
        return "legacy"
    node = extensions.get(NODE_EXTENSION)
    if not isinstance(node, dict):
        return "legacy"
    value = str(node.get("id", "")).strip()
    return value or "legacy"


def _machine_id() -> str:
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            return value[:256]
    return ""
