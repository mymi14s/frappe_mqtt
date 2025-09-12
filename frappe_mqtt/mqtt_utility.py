from __future__ import annotations

import hashlib, json, re, ssl, threading, uuid, time, frappe,signal
from dataclasses import dataclass, asdict
from datetime import datetime
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, Set

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

from .db import DBFrappe, frappe_db
from .utils import push_webhook

JsonDict = Dict[str, Any]
MessageHandler = Callable[[mqtt.Client, Any, JsonDict, mqtt.MQTTMessage], None]


def _topic_match(filter_str: str, topic: str) -> bool:
    """Return True if topic matches MQTT-style filter (+/#)."""
    pattern = re.escape(filter_str).replace(r"\+", "[^/]+").replace(r"\#", ".*")
    return re.fullmatch(pattern, topic) is not None



@dataclass(frozen=True)
class MQTTConfig:
    """Hold MQTT connection parameters."""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    ca_certs: Optional[str] = None
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    tls_insecure: bool = False
    keepalive: int = 60
    clean_session: bool = True
    error_topic: Optional[str] = None

    def fingerprint(self) -> str:
        """Return a stable hash of the configuration."""
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class MQTTClient:
    """Wrap a Paho v5 client with validation, hot-reload, and helpers."""

    DBFrappe = None

    def __init__(self, config: MQTTConfig, *, client_id: Optional[str] = None, connect_immediately: bool = True) -> None:
        """Initialize the client and optionally connect."""
        self.config = config
        self._client_id = client_id or f"frappe-mqtt-{uuid.uuid4().hex[:10]}"
        self._client = self._build_paho_client()
        self._lock = threading.RLock()
        self._subscriptions: List[Tuple[str, int]] = []
        self._message_handlers: List[MessageHandler] = []
        self.status: Dict[str, Any] = {"connected": False, "rc": None, "last_connect": None, "last_disconnect": None}
        self._client.on_connect = self._on_connect_wrapper
        self._client.on_disconnect = self._on_disconnect_wrapper
        self._client.on_message = self._on_message_wrapper
        if connect_immediately:
            self.connect()

    def _build_paho_client(self) -> mqtt.Client:
        """Return a configured Paho v5/v3 client."""
        c = None

        try:
            c = mqtt.Client(
                client_id=self._client_id,
                protocol=mqtt.MQTTv5,
                transport="tcp",
                callback_api_version=CallbackAPIVersion.VERSION2,
            )
            print("Created MQTT client with MQTTv5")

        except Exception as e1:
            print(f"Failed with MQTTv5: {e1}")

            try:
                c = mqtt.Client(
                    client_id=self._client_id,
                    protocol=mqtt.MQTTv311,
                    transport="tcp",
                    callback_api_version=CallbackAPIVersion.VERSION2,
                )
                print("Created MQTT client with MQTTv311")

            except Exception as e2:
                print(f"Failed with MQTTv311: {e2}")

                try:
                    c = mqtt.Client(
                        client_id=self._client_id,
                        protocol=mqtt.MQTTv31,
                        transport="tcp",
                        callback_api_version=CallbackAPIVersion.VERSION2,
                    )
                    print("Created MQTT client with MQTTv31")

                except Exception as e3:
                    print(f"Failed with MQTTv31: {e3}")
                    c = None

        if not c:
            raise Exception("Client not initialized.")

        if self.config.username and self.config.password:
            c.username_pw_set(self.config.username, self.config.password)
        if self.config.ca_certs or self.config.certfile or self.config.keyfile:
            c.tls_set(
                ca_certs=self.config.ca_certs,
                certfile=self.config.certfile,
                keyfile=self.config.keyfile,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            c.tls_insecure_set(bool(self.config.tls_insecure))
        return c

    def connect(self) -> None:
        """Connect and start the background loop."""
        with self._lock:
            self._client.connect(self.config.host, self.config.port, keepalive=self.config.keepalive)
            self._client.loop_start()

    def disconnect(self) -> None:
        """Disconnect and stop the background loop."""
        with self._lock:
            try:
                self._client.loop_stop()
            finally:
                self._client.disconnect()

    @property
    def client(self) -> mqtt.Client:
        """Return the underlying Paho client."""
        return self._client

    def reload(self, new_config: MQTTConfig) -> None:
        """Rebuild Paho client if configuration changed."""
        with self._lock:
            if new_config.fingerprint() == self.config.fingerprint():
                return
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass
            self.config = new_config
            self._client = self._build_paho_client()
            self._client.on_connect = self._on_connect_wrapper
            self._client.on_disconnect = self._on_disconnect_wrapper
            self._client.on_message = self._on_message_wrapper
            self._client.connect(self.config.host, self.config.port, keepalive=self.config.keepalive)
            self._client.loop_start()
            for topic, qos in list(self._subscriptions):
                try:
                    self._client.subscribe(topic, qos=qos)
                except Exception as exc:
                    frappe.throw(repr(exc))
                    
    def publish(self, topic: str, payload: Dict[str, Any], qos: int = 0, retain: bool = False):
        """Publish a dict payload enriched with metadata."""
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        enriched = dict(payload)
        enriched.setdefault("timeStamp", datetime.now().isoformat())
        data = json.dumps(enriched, separators=(",", ":"))
        return self._client.publish(topic=topic, payload=data, qos=qos, retain=retain)

    def subscribe(self, topic: str, qos: int = 0):
        """Subscribe and track the topic for automatic re-subscription."""
        with self._lock:
            if (topic, qos) not in self._subscriptions:
                self._subscriptions.append((topic, qos))
        return self._client.subscribe(topic, qos=qos)

    def snapshot_retained(self, topic_filter: str, *, limit: int = 100, timeout_sec: float = 1.5,
                          include_live_during_window: bool = True) -> List[dict]:
        """Return a retained snapshot (and optional live window) for a filter."""
        results: List[dict] = []
        done = Event()

        def collector(c, u, msg_dict, raw):
            if not _topic_match(topic_filter, raw.topic):
                return
            entry = {
                "topic": raw.topic,
                "qos": int(getattr(raw, "qos", 0) or 0),
                "retain": int(getattr(raw, "retain", 0) or 0),
                "timestamp": datetime.now().isoformat(),
                "payload": msg_dict,
            }
            if entry["retain"] == 1 or include_live_during_window:
                results.append(entry)
                if len(results) >= limit:
                    done.set()

        self.add_on_message_handler(collector)
        try:
            self.subscribe(topic_filter, qos=0)
            done.wait(timeout_sec)
        finally:
            try:
                self._client.unsubscribe(topic_filter)
            except Exception:
                pass
            try:
                self._message_handlers.remove(collector)
            except ValueError:
                pass

        latest_by_topic = {}
        for r in results:
            latest_by_topic[r["topic"]] = r
        out = list(latest_by_topic.values())
        out.sort(key=lambda r: r["timestamp"], reverse=True)
        return out[:limit]

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """Handle successful connection (override if needed)."""
        rc = int(getattr(reason_code, "value", reason_code))
        self.status.update({"connected": True, "rc": rc, "last_connect": datetime.now().isoformat()})
        

    def on_disconnect(self, client, userdata, reason_code, properties) -> None:
        """Handle disconnection (override if needed)."""
        rc = int(getattr(reason_code, "value", reason_code))
        self.status.update({"connected": False, "rc": rc, "last_disconnect": datetime.now().isoformat()})

    def on_message(self, client: mqtt.Client, userdata: Any, message_dict: JsonDict, raw: mqtt.MQTTMessage) -> None:
        """Handle validated incoming message (override if needed)."""
        pass
        
        
    def process_web_hook(self, topic, payload):
        """Handle data posting to webhook."""
        if topic in self.webhooks:
            push_webhook(self.webhooks[topic], payload.update({"_topic_":topic}))

    def add_on_message_handler(self, handler: MessageHandler) -> None:
        """Register an additional message handler."""
        if not callable(handler):
            raise TypeError("handler must be callable(client, userdata, message_dict, raw_message)")
        self._message_handlers.append(handler)

    def _on_connect_wrapper(self, client, userdata, flags, reason_code, properties) -> None:
        """Wrap Paho on_connect to call the user handler safely."""
        try:
            self.on_connect(client, userdata, flags, reason_code, properties)
        except Exception as exc:
            repr(exc)

    def _on_disconnect_wrapper(self, client, userdata, reason_code, properties, others=None) -> None:
        """Wrap Paho on_disconnect to call the user handler safely."""
        try:
            self.on_disconnect(client, userdata, reason_code, properties)
        except Exception as exc:
            frappe.throw(repr(exc))

    def _on_message_wrapper(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        """Validate payload and dispatch realtime + handlers."""
        parsed: Optional[Union[JsonDict, Any]] = None
        try:
            if isinstance(msg.payload, (bytes, bytearray)):
                s = msg.payload.decode("utf-8", errors="ignore").strip()
                if s:
                    parsed = json.loads(s)
            elif isinstance(msg.payload, str):
                parsed = json.loads(msg.payload)
            elif isinstance(msg.payload, dict):
                parsed = msg.payload
        except Exception:
            parsed = None

        if not isinstance(parsed, dict):
            if self.config.error_topic:
                try:
                    self.publish(self.config.error_topic, {"topic": msg.topic, "reason": "invalid_json"})
                except Exception:
                    pass
            return

        try:
            self.process_web_hook(msg.topic, parsed)
            self.on_message(client, userdata, parsed, msg)
        except Exception as exc:
            frappe.log_error(f"MQTT on_message error: {exc}", "Frappe MQTT")

        for h in list(self._message_handlers):
            try:
                h(client, userdata, parsed, msg)
            except Exception as exc:
                frappe.log_error(f"MQTT handler error: {exc}", "Frappe MQTT")

    def flush_retained(self, topic: Optional[str] = None, *, clear_all: bool = False, timeout: float = 1.5, qos: int = 0) -> dict:
        """Clear retained message(s) for a specific topic, wildcard, or all topics."""
        if not self.client:
            raise RuntimeError("MQTT client not initialized/connected")

        if topic and all(ch not in topic for ch in ("#", "+")) and not clear_all:
            info = self.client.publish(topic=topic, payload=b"", qos=qos, retain=True)
            info.wait_for_publish()
            return {"cleared": [topic], "count": 1, "mode": "single"}

        topic_filter = "#" if (not topic or clear_all) else topic
        collected: Set[str] = set()
        stop_event = threading.Event()

        def _collector(_client, _userdata, msg: mqtt.MQTTMessage):
            if getattr(msg, "retain", False):
                collected.add(msg.topic)

        try:
            self.client.message_callback_add(topic_filter, _collector)
            self.client.subscribe(topic_filter, qos=qos)
            stop_event.wait(timeout=max(0.2, float(timeout)))
        finally:
            try:
                self.client.message_callback_remove(topic_filter)
            except Exception:
                pass
            try:
                self.client.unsubscribe(topic_filter)
            except Exception:
                pass

        cleared: list[str] = []
        for t in sorted(collected):
            try:
                info = self.client.publish(topic=t, payload=b"", qos=qos, retain=True)
                info.wait_for_publish()
                cleared.append(t)
            except Exception:
                continue

        return {"cleared": cleared, "count": len(cleared), "mode": "all" if clear_all else "wildcard", "filter": topic_filter}


class MultiMQTTClientManager:
    """Manage clients from site_config and active Broker doctypes."""
    site_name = ""
    frappe_db = None
    running = False

    def __init__(self) -> None:
        """Create the manager."""
        self._clients: Dict[str, MQTTClient] = {}
        self._fingerprints: Dict[str, str] = {}
        self._site_fp: Optional[str] = None
        self._lock = threading.RLock()
        

    def _current_site_tuple(self) -> Optional[Tuple[str, MQTTConfig, str]]:
        """Return ('site_config', config, fingerprint) if valid, else None."""
        try:
            sc = frappe.get_site_config() or {}
            self.site_name = frappe.local.site
        except Exception:
            sc = {}

        db_host = sc.get("db_host") or "localhost"
        db_type = sc.get("db_type")
        db_name = sc.get("db_name")
        db_user = db_name
        db_password = sc.get("db_password")

        if all([db_host, db_type, db_name, db_password]):
            self.frappe_db = frappe_db(DBFrappe(db_type=db_type, host=db_host, user = db_user, password = db_password))

        cfgs = sc.get("mqtt_config") or []
        rows = []
        for cfg in cfgs:
            if cfg.get("host") and cfg.get("port"):
                cfg["isfile"] = 1
                rows.append(cfg)
        return rows

    def _load_active_brokers(self) -> List[Tuple[str, MQTTConfig]]:
        """Return active brokers as (key, config) pairs."""
        rows = self._current_site_tuple()
        rows += frappe.get_all(
            "MQTT Broker",
            filters={"active": 1},
            fields=[
                "name", "host", "port", "username", "tls_insecure",
                "keepalive", "clean_session", "error_topic",
                "ca_certs", "certfile", "keyfile",
            ],
        )
        out: List[Tuple[str, MQTTConfig]] = []
        for r in rows:
            if not r.get("host") or r.get("port") is None:
                continue
            pwd = None
            try:
                if not r.get("isfile"):
                    doc = frappe.get_doc("MQTT Broker", r["name"])
                    pwd = doc.get_password("password") if doc.get("password") else None
                else:
                    pwd = r.get("password") or None
            except Exception:
                pwd = None
            mc = MQTTConfig(
                host=r["host"],
                port=int(r["port"]),
                username=r.get("username") or None,
                password=pwd,
                ca_certs=r.get("ca_certs") or None,
                certfile=r.get("certfile") or None,
                keyfile=r.get("keyfile") or None,
                tls_insecure=bool(r.get("tls_insecure", 0)),
                keepalive=int(r.get("keepalive") or 60),
                clean_session=bool(r.get("clean_session", 1)),
                error_topic=r.get("error_topic") or None,
            )
            out.append((r["name"], mc))
        
        return out

    def ensure_clients(self) -> Dict[str, MQTTClient]:
        """Ensure clients from both sources are running and subscribed."""
        with self._lock:
            desired: Dict[str, MQTTConfig] = {}

            for key, cfg in _site_config_brokers():
                desired[key] = cfg

            for key, cfg in self._load_active_brokers():
                desired[key] = cfg

            for key, cfg in desired.items():
                fp = cfg.fingerprint()
                if key not in self._clients:
                    self._clients[key] = MQTTClient(cfg)
                    self._clients[key].frappe_db = self.frappe_db
                    self._clients[key].site_name = self.site_name
                    self._fingerprints[key] = fp
                elif self._fingerprints.get(key) != fp:
                    self._clients[key].reload(cfg)
                    self._clients[key].frappe_db = self.frappe_db
                    self._clients[key].site_name = self.site_name
                    self._fingerprints[key] = fp

            for key in list(self._clients.keys()):
                if key not in desired:
                    try:
                        self._clients[key].disconnect()
                    except Exception:
                        pass
                    self._clients.pop(key, None)
                    self._fingerprints.pop(key, None)

            self.update_subscriptions_all()
            self.running = True
            return dict(self._clients)
        

    def reload_all(self) -> None:
        """Re-evaluate both sources and apply changes."""
        self.ensure_clients()

    def reload_site_config(self) -> None:
        """Re-evaluate site_config and apply changes."""
        self.ensure_clients()

    def update_subscriptions_all(self) -> None:
        """Subscribe all clients to enabled topics."""
        webhooks = {}
        rows = frappe.get_all("MQTT Topic", filters={"enabled": 1}, fields=["topic", "qos", "broker", "webhook_url"], order_by="topic asc")
        if not rows:
            return
        for key, client in self._clients.items():
            for r in rows:
                try:
                    if r["broker"]:
                        if r["broker"] == key:
                            client.subscribe(r["topic"], qos=int(r.get("qos") or 0))
                    else:
                        client.subscribe(r["topic"], qos=int(r.get("qos") or 0))
                    if r["webhook_url"]:
                        webhooks[r["topic"]] = r["webhook_url"]
                except Exception as exc:
                    frappe.throw(f"MQTT: subscribe failed: {exc}")

        self.webhooks = webhooks
        for key, client in self._clients.items():
            client.webhooks = webhooks

    def get_clients(self) -> Dict[str, MQTTClient]:
        """Return the managed clients, creating them if needed."""
        if not self._clients:
            self.ensure_clients()
        return self._clients

    def get_client(self, key_preference: Optional[str] = None) -> Optional[MQTTClient]:
        """Return a preferred client or a default one."""
        clients = self.get_clients()
        if not clients:
            return None
        if key_preference and key_preference in clients:
            return clients[key_preference]
        if "site_config" in clients:
            return clients["site_config"]
        return clients[sorted(clients.keys())[0]]

    def snapshot_any(self, topic_filter: str, limit: int = 100, timeout_sec: float = 1.5,
                     client_key: Optional[str] = None) -> List[dict]:
        """Return retained snapshot using a specific or default client."""
        c = self.get_client(client_key)
        if not c:
            return []
        return c.snapshot_retained(topic_filter, limit=limit, timeout_sec=timeout_sec)


_multi = MultiMQTTClientManager()


def ensure_clients_ready() -> None:
    """Ensure all clients exist and are subscribed."""
    _multi.ensure_clients()


@frappe.whitelist()
def reload_all_clients() -> None:
    """Reload clients for both sources."""
    _multi.reload_all()


def reload_site_config_clients(*_a, **_k) -> None:
    """Reload clients for the site_config source."""
    _multi.reload_site_config()


def refresh_subscriptions_from_doctype() -> None:
    """Resubscribe all clients from MQTT Topic records."""
    _multi.update_subscriptions_all()


def get_clients() -> Dict[str, MQTTClient]:
    """Return the client mapping."""
    return _multi.get_clients()


def get_client(prefer: Optional[str] = None) -> MQTTClient:
    """Return one client or raise if none exist."""
    c = _multi.get_client(prefer)
    if not c:
        raise RuntimeError("No MQTT clients are configured.")
    return c


def get_broker_history(topic_filter: str, limit: int = 100, timeout_sec: float = 1.5, client_key: Optional[str] = None) -> List[dict]:
    """Return a retained snapshot from a specific or default client."""
    return _multi.snapshot_any(topic_filter, limit=limit, timeout_sec=timeout_sec, client_key=client_key)


def _site_config_brokers() -> List[Tuple[str, MQTTConfig]]:
    """Return brokers from site_config in either single- or multi-broker format."""
    try:
        sc = frappe.get_site_config() or {}
    except Exception:
        return []

    cfgs = sc.get("mqtt_config")
    if not cfgs:
        return []

    out: List[Tuple[str, MQTTConfig]] = []

    def build_conf(d: Dict[str, Any]) -> MQTTConfig:
        return MQTTConfig(
            host=str(d["host"]),
            port=int(d["port"]),
            username=d.get("username") or None,
            password=d.get("password") or None,
            ca_certs=d.get("ca_certs") or d.get("cafile") or None,
            certfile=d.get("certfile") or None,
            keyfile=d.get("keyfile") or None,
            tls_insecure=bool(d.get("tls_insecure", 0)),
            keepalive=int(d.get("keepalive") or 60),
            clean_session=True,
            error_topic=d.get("error_topic") or None,
        )

    for cfg in cfgs:
        if isinstance(cfg, dict) and "host" in cfg and "name" in cfg and "port" in cfg:
            out.append((cfg.get("name"), build_conf(cfg)))
    return out


_shutdown = threading.Event()

def _graceful_shutdown(signum, frame):
    try:
        log_text = f"[frappe_mqtt] Received signal {signum}; shutting down..."
        frappe.logger().info(log_text)
    except Exception:
        pass
    _shutdown.set()

def run_forever(healthcheck_interval_sec: int = 60):
    """
    Production entrypoint: ensure clients exist, then block the main thread.
    """
    reload_all_clients()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _graceful_shutdown)

    def _reload(_s, _f):
        try:
            log_text = "[frappe_mqtt] SIGHUP received; reloading clients/subscriptions..."
            print(log_text)
            frappe.logger().info(log_text)
        except Exception:
            pass
        try:
            reload_all_clients()
        except Exception as exc:
            frappe.log_error(f"Reload failed: {exc}", "Frappe MQTT")
    try:
        signal.signal(signal.SIGHUP, _reload)
    except Exception:
        pass

    try:
        log_text = "[frappe_mqtt] Clients started; entering wait loop."
        print(log_text)
        frappe.logger().info(log_text)
    except Exception:
        pass

    while not _shutdown.is_set():
        print("health check.........")
        _shutdown.wait(timeout=max(5, int(healthcheck_interval_sec)))
        if not _shutdown.is_set():
            try:
                ensure_clients_ready()
            except Exception as exc:
                log_text = f"ensure_clients_ready failed: {exc}", "Frappe MQTT"
                print(log_text)
                frappe.log_error(log_text)

    try:
        for c in list(get_clients().values()):
            try:
                c.disconnect()
            except Exception:
                pass
        log_text = "[frappe_mqtt] Stopped cleanly."
        print(log_text)
        frappe.logger().info(log_text)
    except Exception:
        pass
