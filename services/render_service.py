from dotenv import load_dotenv
load_dotenv()
import os
import requests

RENDER_API_KEY = os.getenv("RENDER_API_KEY")
BASE_URL = "https://api.render.com/v1"

def _headers():
    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json"
    }

def get_services():
    url = f"{BASE_URL}/services?limit=20"
    res = requests.get(url, headers=_headers(), timeout=15)
    res.raise_for_status()
    return res.json()

def get_service_logs(service_id: str, limit: int = 100) -> str:
    url = f"{BASE_URL}/services/{service_id}/logs?limit={limit}"
    res = requests.get(url, headers=_headers(), timeout=15)
    if res.status_code == 200:
        data = res.json()
        if isinstance(data, list):
            return "\n".join([entry.get("message", "") for entry in data])
        return str(data)
    return f"Ошибка получения логов: {res.status_code}"

def restart_service(service_id: str):
    url = f"{BASE_URL}/services/{service_id}/deploys"
    res = requests.post(url, headers=_headers(), json={"clearCache": "do_not_clear"}, timeout=15)
    res.raise_for_status()
    return res.json()

