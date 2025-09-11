import os
from frappe.utils import get_bench_path

PROCFILE_PATH = os.path.join(get_bench_path(), "Procfile")
PROCESS_LINE = "frappe_mqtt: bench execute frappe_mqtt.runner.start\n"

def after_install():
    """Add frappe_mqtt process to Procfile"""
    if not os.path.exists(PROCFILE_PATH):
        return

    with open(PROCFILE_PATH, "r") as f:
        lines = f.readlines()

    if any(line.startswith("frappe_mqtt:") for line in lines):
        return 

    with open(PROCFILE_PATH, "a") as f:
        f.write(PROCESS_LINE)




