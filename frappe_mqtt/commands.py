import click
import os
from frappe.utils import get_bench_path
from frappe_mqtt.mqtt_utility import reload_all_clients

PROCFILE_PATH = os.path.join(get_bench_path(), "Procfile")
PROCESS_LINE = "frappe_mqtt: bench --site all execute frappe_mqtt.runner.start\n"


@click.command("frappe-mqtt", help="MQTT utilities")
@click.argument("action")
def frappe_mqtt(action):
    if action == "ensure-client":
        ensure_client()
    else:
        click.secho(f"Unknown action: {action}", fg="red")


def ensure_client():
    """Ensure frappe_mqtt process is present in Procfile"""
    if not os.path.exists(PROCFILE_PATH):
        click.secho("No Procfile found in bench root", fg="red")
        return

    with open(PROCFILE_PATH, "r") as f:
        lines = f.readlines()

    if any(line.startswith("frappe_mqtt:") for line in lines):
        click.secho("frappe_mqtt process already in Procfile", fg="green")
        return

    with open(PROCFILE_PATH, "a") as f:
        f.write(PROCESS_LINE)

    click.secho("Added frappe_mqtt process to Procfile", fg="green")



@click.command("frappe-mqtt", help="MQTT utilities")
@click.argument("action")
def start(action):
    if action == "start":
        reload_all_clients()
    else:
        click.secho(f"Unknown action: {action}", fg="red")

commands = [
    frappe_mqtt,
    start
]