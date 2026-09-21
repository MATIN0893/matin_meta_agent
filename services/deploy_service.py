import requests
from config.settings import RENDER_DEPLOY_HOOK

def trigger_deploy(service_name: str = None) -> str:
    resp = requests.post(RENDER_DEPLOY_HOOK)
    if resp.status_code == 200:
        return "✅ Деплой запущен"
    return f"❌ Render ошибка: {resp.status_code}"
