from __future__ import annotations

import json, requests
from typing import Optional

import frappe
from frappe import _
from frappe import request
from frappe.utils.response import Response

from .mqtt_utility import get_broker_history as _hist
from .mqtt_utility import get_client, get_clients, reload_all_clients, _multi


def _require_mqtt_role() -> None:
    """Ensure the caller is authenticated and has MQTT role."""
    if frappe.session.user in ("Guest", None):
        frappe.throw(_("Authentication required"), frappe.PermissionError)
    if not "MQTT" in frappe.get_roles(frappe.session.user):
        frappe.throw(_("You do not have permission to access MQTT."), frappe.PermissionError)


@frappe.whitelist()
def clients() -> dict:
    """Return available MQTT client keys."""
    _require_mqtt_role()
    return {"clients": list(get_clients().keys())}


@frappe.whitelist()
def publish(topic: str, payload_json: str, qos: int = 0, retain: int = 0,
            client_key: Optional[str] = None, broadcast: int = 0) -> dict:
    """Publish to a specific client or broadcast to all."""
    _require_mqtt_role()
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except Exception:
        frappe.throw(_("payload_json must be valid JSON"))
    if int(broadcast or 0):
        sent = []
        for key, c in get_clients().items():
            c.publish(topic, payload, qos=int(qos or 0), retain=bool(retain))
            sent.append(key)
        return {"ok": True, "sent_to": sent}
    c = get_client(client_key)
    c.publish(topic, payload, qos=int(qos or 0), retain=bool(retain))
    return {"ok": True, "client": client_key or "default"}


@frappe.whitelist()
def broker_history(topic: str, limit: int = 100, timeout_ms: int = 1500, client_key: Optional[str] = None) -> list:
    """Return retained snapshot for a topic filter."""
    _require_mqtt_role()
    timeout_sec = max(0.2, (int(timeout_ms) or 1500) / 1000.0)
    return _hist(topic_filter=topic, limit=int(limit or 100), timeout_sec=timeout_sec, client_key=client_key)


@frappe.whitelist()
def flush_retained(client_key: str | None = None, topic: str | None = None, clear_all: int = 0, timeout: float = 1.5, qos: int = 0):
    """Flush retained message(s) via HTTP/Desk, returns summary."""
    _require_mqtt_role()
    ckey = client_key or "default"
    client = get_client(ckey)
    return client.flush_retained(topic=topic, clear_all=bool(int(clear_all)), timeout=timeout, qos=qos)



@frappe.whitelist(allow_guest=True)
def start_mqtt():
    if not _multi.running:
        reload_all_clients()