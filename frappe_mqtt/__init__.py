from frappe import app
from .boot import new_run_after_request_hooks

__version__ = "0.0.1"

app.run_after_request_hooks = new_run_after_request_hooks


