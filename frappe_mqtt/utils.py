import requests, threading

def push_webhook(url, data, headers=None, callback=None):
    """
    Sends a POST request with JSON payload in a background thread.

    Args:
        url (str): The endpoint URL.
        data (dict): The JSON payload to send.
        headers (dict, optional): Additional headers (defaults to {"Content-Type": "application/json"}).
        callback (function, optional): Function to call with response after request completes.
    """
    def task():
        try:
            response = requests.post(url, json=data, headers=headers or {"Content-Type": "application/json"})
            if callback:
                callback(response)
        except Exception as e:
            if callback:
                callback(e)

    thread = threading.Thread(target=task, daemon=True)
    thread.start()
    return thread
