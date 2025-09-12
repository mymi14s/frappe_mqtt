import frappe

def create_data():
    """Create or update the default MQTT Broker"""
    doc = frappe.get_doc({
        "doctype": "MQTT Broker",
        "name": "default",
        "broker_name": "default",
        "active": 1,
        "host": "localhost",
        "port": 1883,
        "keepalive": 60,
        "tls_insecure": 0,
        "clean_session": 1,
    })
    doc.insert(ignore_permissions=True)

    doc = frappe.get_doc({
        "doctype": "MQTT Topic",
        "name": "frappe/hello",
        "enabled": 1,
        "topic": "frappe/hello",
        "broker": "default",
        "qos": "0",
        "webhook_url": "http://localhost:8000"
    })
    doc.insert(ignore_permissions=True)

    frappe.db.commit()
