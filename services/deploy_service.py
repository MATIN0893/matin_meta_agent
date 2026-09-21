import time
import requests
from config.settings import RENDER_DEPLOY_HOOK

def trigger_deploy() -> str:
    """Запускает деплой проекта через Deploy Hook."""
    if not RENDER_DEPLOY_HOOK:
        return "Деплой пропущен: RENDER_DEPLOY_HOOK не задан"
    try:
        resp = requests.post(RENDER_DEPLOY_HOOK, timeout=20)
        if resp.status_code in (200, 201):
            return "✅ Деплой запущен"
        return f"⚠️ Ошибка запуска деплоя: HTTP {resp.status_code}"
    except Exception as e:
        return f"❌ Ошибка вызова Render Hook: {e}"

def check_render_deploy_status(service_id: str, api_key: str) -> dict:
    """
    Health Check: проверяет статус сборки и деплоя через официальный API Render.
    Возможные статусы: build_in_progress, live, build_failed, update_failed, canceled.
    """
    if not service_id or not api_key:
        return {
            "success": False,
            "status": "not_configured",
            "message": "Render API Key или Service ID не настроены."
        }

    url = f"https://api.render.com/v1/services/{service_id}/deploys?limit=1"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return {
                "success": False,
                "status": f"http_{resp.status_code}",
                "message": f"Ошибка ответа Render API: {resp.text}"
            }

        deploys = resp.json()
        if not deploys:
            return {
                "success": False,
                "status": "empty",
                "message": "Список деплоев пуст."
            }

        latest_deploy = deploys[0].get("deploy", {})
        status = latest_deploy.get("status", "unknown")
        commit = latest_deploy.get("commit", {}).get("message", "Без описания")

        return {
            "success": True,
            "status": status,
            "commit": commit,
            "created_at": latest_deploy.get("createdAt"),
            "finished_at": latest_deploy.get("finishedAt")
        }
    except Exception as e:
        return {
            "success": False,
            "status": "error",
            "message": f"Ошибка соединения с Render: {e}"
        }