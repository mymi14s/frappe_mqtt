import os
import frappe
from frappe.utils import get_bench_path

def start():
    """Entry point for frappe_mqtt process"""
    dev_mode = frappe.conf.get("developer_mode") or os.environ.get("DEV_SERVER", "0") == "1"

    if dev_mode:
        _start_with_reloader()
    else:
        _start_clients()


def _start_clients():
    """Run MQTT clients for installed sites"""
    from frappe.utils import get_sites
    from frappe_mqtt.mqtt_utility import reload_all_clients

    sites = get_sites()
    for site in sites:      
        try:
            site_path = os.path.join(get_bench_path(), "sites", site)
            if os.path.exists(os.path.join(site_path, "site_config.json")):
                frappe.init(site=site)
                frappe.connect()
                if "frappe_mqtt" in frappe.get_installed_apps():
                    frappe.logger().info(f"[frappe_mqtt] Starting MQTT clients for site {site}")
                    reload_all_clients()
        finally:
            frappe.destroy()


def _start_with_reloader():
    """Wrap client in Frappe hot-reload loop during development"""
    from werkzeug._reloader import run_with_reloader
    frappe.logger().info("[frappe_mqtt] Development mode: running with hot reload")
    run_with_reloader(_start_clients)
