import time
import logging
from config.settings import TELEGRAM_ADMIN_ID, is_user_allowed
from services.health_service import update_patrol_heartbeat
from services.render_service import get_services, get_service_logs, restart_service
from engine.self_heal import attempt_self_heal
from core.llm_client import check_llm_health

try:
    from telegram import Bot
except ImportError:
    class Bot:
        pass

logger = logging.getLogger("MATIN.PATROL")

class ServiceState:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RESTARTING = "restarting"
    WAITING_TOKEN = "waiting_token"

class RuntimeService:
    def __init__(self, name: str, service_id: str):
        self.name = name
        self.service_id = service_id
        self.state = ServiceState.HEALTHY
        self.last_check = 0.0
        self.last_error = ""
        self.consecutive_errors = 0
        self.last_notified = 0.0

STATE = {}

_last_brain_check = 0.0
_cached_brain_status = True


def record_brain_success():
    global _last_brain_check, _cached_brain_status
    _last_brain_check = time.time()
    _cached_brain_status = True


def check_self_brain() -> bool:
    global _last_brain_check, _cached_brain_status
    now = time.time()
    if now - _last_brain_check < 60:
        return _cached_brain_status
    try:
        res = check_llm_health()
        _cached_brain_status = bool(res)
        _last_brain_check = now
        return _cached_brain_status
    except Exception as e:
        logger.error(f"[SELF-CHECK] Мозг недоступен: {e}")
        _cached_brain_status = False
        _last_brain_check = now
        return False


def classify_logs(logs: str) -> str:
    text = logs.lower()
    if any(k in text for k in ["invalidtoken", "401 unauthorized", "invalid api key", "token is invalid"]):
        return "TOKEN"
    if any(k in text for k in ["modulenotfounderror", "syntaxerror", "importerror", "attributeerror"]):
        return "CODE_CRASH"
    if any(k in text for k in ["connection refused", "502 bad gateway", "timed out", "service unavailable"]):
        return "NETWORK"
    return "UNKNOWN"


def patrol_tick(bot):
    try:
        services_data = get_services()
    except Exception as e:
        logger.error(f"[PATROL] Ошибка получения сервисов Render: {e}")
        update_patrol_heartbeat(success=False, error_msg=str(e))
        return

    now = time.time()

    for item in services_data:
        srv = item.get("service", {})
        s_id = srv.get("id")
        s_name = srv.get("name")
        if not s_id or not s_name:
            continue

        if s_name not in STATE:
            STATE[s_name] = RuntimeService(s_name, s_id)

        target = STATE[s_name]
        target.last_check = now

        logs = get_service_logs(s_id, limit=80)
        category = classify_logs(logs)

        if category == "TOKEN":
            target.state = ServiceState.WAITING_TOKEN
            target.last_error = "Требуется ручной ввод токена"
            if now - target.last_notified > 1800:
                _alert(bot, f"🚨 Сервис `{s_name}` требует токен! Проверь переменные окружения на Render.")
                target.last_notified = now

        elif category == "CODE_CRASH":
            target.state = ServiceState.DEGRADED
            target.consecutive_errors += 1
            logger.warning(f"[PATROL] Обнаружен краш кода в {s_name}. Запуск Self-Heal...")

            heal_result = attempt_self_heal(s_name, logs)
            if heal_result.get("success"):
                _alert(bot, f"🛠 **Self-Heal:** Применен патч для `{s_name}`! Коммит отправлен в GitHub.")
                target.state = ServiceState.HEALTHY
                target.consecutive_errors = 0
            else:
                if target.consecutive_errors <= 2:
                    logger.info(f"[PATROL] Перезапуск сервиса {s_name}...")
                    try:
                        restart_service(s_id)
                        _alert(bot, f"⚠️ Сервис `{s_name}` перезапущен после сбоя.")
                    except Exception as re:
                        logger.error(f"[PATROL] Не удалось перезапустить {s_name}: {re}")

        elif category == "NETWORK":
            target.state = ServiceState.DEGRADED
            if target.consecutive_errors < 1:
                try:
                    restart_service(s_id)
                    _alert(bot, f"📡 Сетевой сбой в `{s_name}`. Отправлен сигнал на перезапуск.")
                except Exception:
                    pass
            target.consecutive_errors += 1

        else:
            target.state = ServiceState.HEALTHY
            target.consecutive_errors = 0

    update_patrol_heartbeat(success=True)


def _alert(bot, text: str):
    if not bot or not TELEGRAM_ADMIN_ID:
        return
    try:
        import asyncio
        asyncio.create_task(bot.send_message(chat_id=int(TELEGRAM_ADMIN_ID), text=text, parse_mode="Markdown"))
    except Exception as e:
        logger.error(f"[PATROL] Не удалось отправить алерт: {e}")
