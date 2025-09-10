import frappe, requests
from .mqtt_utility import _multi


def start_mqtt():
    if not _multi.running:
        requests.get(frappe.utils.get_url()+"/api/method/frappe_mqtt.api.start_mqtt")