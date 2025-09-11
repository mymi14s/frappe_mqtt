import os

PROCFILE_PATH = os.path.join(os.getcwd(), "Procfile")
PROCESS_LINE = "frappe_mqtt: bench --site all execute frappe_mqtt.runner:start\n"

def after_install():
    """Add frappe_mqtt process to Procfile"""
    if not os.path.exists(PROCFILE_PATH):
        return

    with open(PROCFILE_PATH, "r") as f:
        lines = f.readlines()

    if any(line.startswith("frappe_mqtt:") for line in lines):
        return  # already added

    with open(PROCFILE_PATH, "a") as f:
        f.write(PROCESS_LINE)


def before_uninstall():
    """Remove frappe_mqtt process from Procfile"""
    if not os.path.exists(PROCFILE_PATH):
        return

    with open(PROCFILE_PATH, "r") as f:
        lines = f.readlines()

    new_lines = [line for line in lines if not line.startswith("frappe_mqtt:")]

    with open(PROCFILE_PATH, "w") as f:
        f.writelines(new_lines)
