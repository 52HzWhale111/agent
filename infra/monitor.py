"""HTTP checks and generic JSON webhook alerts; no Docker socket access."""
import json
import os
import time
import urllib.error
import urllib.request

TARGETS = {
    "gateway": "http://gateway:8000/health/ready",
    "knowledge": "http://knowledge:8100/health",
    "sandbox": "http://sandbox:8200/health",
    "backend": "http://backend:8000/health",
    "frontend": "http://frontend:3000/",
}


def check(url):
    try:
        headers = {"Authorization": "Bearer " + os.getenv("GATEWAY_API_KEY", "")}
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
            return response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def alert(service, healthy):
    payload = {"service": service, "status": "recovered" if healthy else "unavailable", "time": time.time()}
    print(json.dumps(payload), flush=True)
    webhook = os.getenv("ALERT_WEBHOOK_URL")
    if not webhook:
        return True
    try:
        request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5):
            return True
    except OSError:
        print(json.dumps({"event": "alert_delivery_failed", "service": service}), flush=True)
        return False


def main():
    previous = {}
    while True:
        for name, url in TARGETS.items():
            healthy = check(url)
            if name not in previous and healthy:
                previous[name] = True
            elif previous.get(name) != healthy and alert(name, healthy):
                previous[name] = healthy
        time.sleep(30)


if __name__ == "__main__":
    main()
