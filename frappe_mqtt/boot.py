from frappe.app import run_after_request_hooks
from .utility import _multi, reload_all_clients

def _run_after_request_hooks(func) -> None:
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        reload_all_clients()
        return result
    return wrapper


new_run_after_request_hooks = _run_after_request_hooks(run_after_request_hooks)