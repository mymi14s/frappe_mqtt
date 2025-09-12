import os, frappe
from werkzeug._reloader import run_with_reloader
from frappe.utils import get_bench_path
from .mqtt_utility import run_forever
from frappe.utils import get_sites
from frappe_mqtt.mqtt_utility import reload_all_clients


def start():
    """Entry point for frappe_mqtt process"""
    dev_mode = frappe.conf.get("developer_mode") or os.environ.get("DEV_SERVER", "0") == "1"

    if dev_mode:
        start_with_reloader()
    else:
        run_forever()


def start_clients():
    """Run MQTT clients for installed sites"""

    sites = get_sites()
    for site in sites:      
        try:
            site_path = os.path.join(get_bench_path(), "sites", site)
            if os.path.exists(os.path.join(site_path, "site_config.json")):
                frappe.init(site=site)
                frappe.connect()
                if "frappe_mqtt" in frappe.get_installed_apps():
                    log_text = f"[frappe_mqtt] Starting MQTT clients for site {site}"
                    print(log_text)
                    frappe.logger().info(log_text)
                    reload_all_clients()
        finally:
            frappe.destroy()


def start_with_reloader():
    """Wrap client in Frappe hot-reload loop during development"""
    log_text = "[frappe_mqtt] Development mode: running with hot reload"
    print(log_text)
    frappe.logger().info(log_text)
    run_with_reloader(start_clients)
