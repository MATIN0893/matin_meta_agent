import asyncio
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
import httpx
from telegram import Bot
from services.render_service import get_services, get_service_logs, restart_service
from core.llm_client import call_llm

logger = logging.getLogger("MATIN.PATROL")

PATROL_INTERVAL = int(os.getenv("PATROL_INTERVAL", "900"))
HEALTH_TIMEOUT = float(os.getenv("HEALTH_TIMEOUT", "15"))
ADMIN_CHAT_ID = os.getenv("ADMIN_TELEGRAM_ID", "504627192")

class ServiceState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    WAITING_TOKEN = "waiting_token"

@dataclass
class RuntimeState:
    state: ServiceState = ServiceState.HEALTHY
    failures: int = 0
    last_error: str = ""

# Динамическое состояние для всех обнаруженных сервисов
STATE: dict[str, RuntimeState] = {}

async def notify(bot: Bot, text: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка отправки в TG: {e}")

def check_self_brain() -> bool:
    """Самодиагностика: Мета проверяет свой собственный мозг"""
    try:
        res = call_llm([{"role": "system", "content": "pong"}, {"role": "user", "content": "ping"}])
        return bool(res)
    except Exception as e:
        logger.error(f"[SELF-CHECK] Мозг недоступен: {e}")
        return False

def classify_logs(logs: str) -> str:
    text = logs.lower()
    if any(k in text for k in ["invalidtoken", "401 unauthorized", "invalid api key", "token is invalid"]):
        return "TOKEN"
    if any(k in text for k in ["429", "rate limit", "quota exceeded"]):
        return "RATE_LIMIT"
    if any(k in text for k in ["out of memory", "killed process", "oom"]):
        return "OOM"
    if "traceback" in text or "error" in text:
        return "CODE_ERROR"
    return "UNKNOWN"

async def check_single_service(client: httpx.AsyncClient, bot: Bot, srv_name: str, srv_url: str, render_id: str):
    if srv_name not in STATE:
        STATE[srv_name] = RuntimeState()
    
    st = STATE[srv_name]
    
    # Если URL веб-сервиса известен, проверяем health
    details = ""
    if srv_url:
        try:
            r = await client.get(f"{srv_url}/health", timeout=HEALTH_TIMEOUT)
            if r.status_code == 200:
                if st.state != ServiceState.HEALTHY:
                    st.state = ServiceState.HEALTHY
                    st.failures = 0
                    await notify(bot, f"🟢 *СЕРВИС ВОССТАНОВЛЕН*\n`{srv_name}` снова в строю!")
                return
            details = f"HTTP {r.status_code}"
        except Exception as e:
            details = f"Сеть / таймаут: {e}"
    else:
        # Если это worker/бот без health-эндпоинта, смотрим логи
        details = "Проверка по логам"

    st.failures += 1
    st.last_error = details

    # Реагируем при повторном сбое (State Machine - без спама)
    if st.failures == 2 and st.state != ServiceState.DEGRADED:
        st.state = ServiceState.DEGRADED
        await notify(bot, f"🚨 *ТРЕВОГА ПАТРУЛЯ*\nСервис `{srv_name}` недоступен!\nПричина: `{details}`\nСобираю логи с Render...")
        
        logs = get_service_logs(render_id, limit=50)
        err_type = classify_logs(logs)

        if err_type == "TOKEN":
            st.state = ServiceState.WAITING_TOKEN
            await notify(bot, f"🔐 *ТРЕБУЕТСЯ КЛЮЧ*\nПроект: `{srv_name}`\nОтвалился API токен.\nПришли мне новый ключ в ответ.")
        elif err_type == "OOM":
            await notify(bot, f"💾 *ПАМЯТЬ ПЕРЕПОЛНЕНА (OOM)*\nПроект: `{srv_name}`\nПерезапускаю контейнер на Render...")
            restart_service(render_id)
        elif err_type == "CODE_ERROR":
            await notify(bot, f"🛠 *ОШИБКА В КОДЕ*\nПроект: `{srv_name}`\nНайдена ошибка выполнения. Передаю задачу в Self-Heal...")

async def patrol_loop(bot: Bot):
    logger.info("Автономный патруль запущен. Автообнаружение проектов Render включено.")
    async with httpx.AsyncClient() as client:
        while True:
            # 1. Самопроверка Меты (Self-Check)
            brain_alive = check_self_brain()
            if not brain_alive:
                logger.warning("[SELF-CHECK] Внимание: LLM провайдер Меты не отвечает!")

            # 2. Динамическое получение ВСЕХ проектов из Render аккаунта
            try:
                render_services = get_services()
            except Exception as e:
                logger.error(f"Не удалось получить список сервисов Render: {e}")
                render_services = []

            for item in render_services:
                srv = item.get("service", {})
                name = srv.get("name")
                srv_id = srv.get("id")
                # Извлекаем URL если это web service
                url = srv.get("serviceDetails", {}).get("url") or ""
                
                if name and srv_id:
                    await check_single_service(client, bot, name, url, srv_id)
                    await asyncio.sleep(2)

            await asyncio.sleep(PATROL_INTERVAL)
