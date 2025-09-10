from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import frappe
import paho.mqtt.client as mqtt_utility

from frappe_mqtt import mqtt_utility


class FakePahoClient:
    """Minimal fake Paho client for unit tests."""

    def __init__(self, *args, **kwargs) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.connected = False
        self.looping = False
        self.connected_args = None
        self.subscriptions: List[Tuple[str, int]] = []
        self.published: List[Dict[str, Any]] = []
        self.retained: Dict[str, Any] = {}
        self._owner = None

    def connect(self, host, port, keepalive=60):
        self.connected = True
        self.connected_args = (host, port, keepalive)
        if self.on_connect:
            self.on_connect(self, None, {}, mqtt_utility.ReasonCodes(mqtt_utility.ReasonCodes.CONNACK | 0), None)

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def disconnect(self):
        self.connected = False
        if self.on_disconnect:
            self.on_disconnect(self, None, mqtt_utility.ReasonCodes(mqtt_utility.ReasonCodes.DISCONNECT | 0), None)

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))
        if self._owner:
            for t, payload in list(self.retained.items()):
                if _match(topic, t):
                    msg = SimpleNamespace(topic=t, qos=qos, retain=1, payload=_to_bytes(payload))
                    self._owner._on_message_wrapper(self, None, msg)
        return (0, 1)

    def unsubscribe(self, topic):
        self.subscriptions = [s for s in self.subscriptions if s[0] != topic]
        return (0, 1)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0, mid=len(self.published))


def _to_bytes(payload: Any) -> bytes:
    if isinstance(payload, (dict, list)):
        return json.dumps(payload).encode()
    if isinstance(payload, str):
        return payload.encode()
    if isinstance(payload, bytes):
        return payload
    return str(payload).encode()


def _match(filter_str: str, topic: str) -> bool:
    import re
    pattern = re.escape(filter_str).replace(r"\+", "[^/]+").replace(r"\#", ".*")
    return re.fullmatch(pattern, topic) is not None


def _patch_site_config(host="sc.local", port=1883, error_topic=None):
    conf = {
        "mqtt_config": {
            "host": host,
            "port": port,
            "username": "u",
            "password": "p",
            "keepalive": 30,
            "error_topic": error_topic,
        }
    }
    return patch("frappe.get_site_config", return_value=conf)


def _mk_topic(topic="test/a", qos="0", enabled=1):
    doc = frappe.get_doc({
        "doctype": "MQTT Topic",
        "topic": topic,
        "qos": qos,
        "enabled": enabled,
    })
    doc.insert(ignore_permissions=True)
    return doc


def _mk_broker(name="BRK-1", host="broker.local", port=1883, active=1, error_topic=None):
    doc = frappe.get_doc({
        "doctype": "MQTT Broker",
        "name": name,
        "host": host,
        "port": port,
        "active": active,
        "error_topic": error_topic or "",
    })
    doc.insert(ignore_permissions=True)
    return doc


def _reset_manager():
    m = mqtt_utility._multi
    for c in list(m._clients.values()):
        try:
            c.disconnect()
        except Exception:
            pass
    m._clients.clear()
    m._fingerprints.clear()
    m._site_fp = None


@patch("paho.mqtt.client.Client", FakePahoClient)
def test_clients_created_from_site_config_and_broker():
    _reset_manager()
    _mk_topic("test/a")
    with _patch_site_config():
        _mk_broker(name="BRK-1", host="broker.local", port=1883, active=1)
        mqtt_utility.ensure_clients_ready()
        clients = mqtt_utility.get_clients()
        assert "site_config" in clients
        assert "BRK-1" in clients
        for key, c in clients.items():
            fake: FakePahoClient = c._client
            assert ("test/a", 0) in fake.subscriptions


@patch("paho.mqtt.client.Client", FakePahoClient)
def test_publish_enrichment_and_broadcast():
    _reset_manager()
    _mk_topic("x/y")
    with _patch_site_config():
        _mk_broker(name="BRK-2", host="b2.local", port=1883, active=1)
        mqtt_utility.ensure_clients_ready()
        clients = mqtt_utility.get_clients()
        from frappe_mqtt.api import publish
        with patch("frappe_mqtt.api._require_mqtt_role", return_value=None):
            res = publish(topic="x/y", payload_json='{"k":1}', qos=0, retain=0, client_key=None, broadcast=1)
            assert res["ok"] is True
        for key, wrapper in clients.items():
            fake: FakePahoClient = wrapper._client
            out = [m for m in fake.published if m["topic"] == "x/y"]
            assert out, f"no publish recorded for {key}"
            payload = json.loads(out[-1]["payload"])
            assert payload["k"] == 1
            assert payload["messageOrigin"] == "hello"
            assert "timeStamp" in payload


@patch("paho.mqtt.client.Client", FakePahoClient)
def test_on_message_json_and_nonjson_error_topic():
    _reset_manager()
    _mk_topic("demo/t")
    with _patch_site_config(error_topic="errors/out"):
        mqtt_utility.ensure_clients_ready()
        c = mqtt_utility.get_client("site_config")
        fake: FakePahoClient = c._client
        fake._owner = c

        seen = []

        def capture(event, message, after_commit=True):
            if event == "mqtt_message":
                seen.append(message)

        with patch("frappe.publish_realtime", side_effect=capture):
            msg_ok = SimpleNamespace(topic="demo/t", qos=0, retain=0, payload=b'{"ok":true}')
            c._on_message_wrapper(fake, None, msg_ok)
            assert seen and seen[-1]["payload"]["ok"] is True

            msg_bad = SimpleNamespace(topic="demo/t", qos=0, retain=0, payload=b'not json')
            c._on_message_wrapper(fake, None, msg_bad)
            published = [m for m in fake.published if m["topic"] == "errors/out"]
            assert published, "expected publish to error topic"
            err_payload = json.loads(published[-1]["payload"])
            assert err_payload["reason"] == "invalid_json"


@patch("paho.mqtt.client.Client", FakePahoClient)
def test_snapshot_retained_collects_matches():
    _reset_manager()
    _mk_topic("sensors/#")
    with _patch_site_config():
        mqtt_utility.ensure_clients_ready()
        c = mqtt_utility.get_client("site_config")
        fake: FakePahoClient = c._client
        fake._owner = c
        fake.retained = {
            "sensors/room1/temp": {"t": 21},
            "other/topic": {"x": 1},
        }
        out = mqtt_utility.get_broker_history("sensors/#", limit=10, timeout_sec=0.1, client_key="site_config")
        topics = {r["topic"] for r in out}
        assert "sensors/room1/temp" in topics
        assert "other/topic" not in topics
