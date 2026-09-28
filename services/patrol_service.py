import asyncio
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
import httpx
from services.render_service import get_services, get_service_logs, restart_service
from services.health_service import update_patrol_heartbeat
from engine.agent_registry import agent_registry, AgentLifecycle
from core.llm_client import check_llm_health

try:
    from telegram import Bot
except ImportError:
    class Bot:
        pass

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


STATE: dict[str, RuntimeState] = {}
_last_brain_check = 0.0
_cached_brain_status = True


def record_brain_success():
    global _last_brain_check, _cached_brain_status
    _cached_brain_status = True
    _last_brain_check = time.time()


async def notify(bot: Bot, text: str):
    if not ADMIN_CHAT_ID or not bot:
        return
    try:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[PATROL] Ошибка отправки в TG: {e}")


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
    if any(k in text for k in ["429", "rate limit", "quota exceeded"]):
        return "RATE_LIMIT"
    if any(k in text for k in ["out of memory", "killed process", "oom"]):
        return "OOM"
    if "traceback" in text or "error" in text:
        return "CODE_ERROR"
    return "UNKNOWN"


async def check_single_service(
    client: httpx.AsyncClient,
    bot: Bot,
    srv_name: str,
    srv_url: str,
    render_id: str,
    health_check_path: str = ""
):
    """
    Проверяет сервис с учетом реального сконфигурированного endpoint на Render.
    Не маскирует HTTP 404 как OK.
    """
    if srv_name not in STATE:
        STATE[srv_name] = RuntimeState()

    st = STATE[srv_name]
    details = ""
    is_up = False
    last_status = None

    if srv_url:
        base = srv_url.rstrip("/")
        # Определяем список endpoint для проверки
        configured_path = health_check_path.strip() if health_check_path else ""
        endpoints = []
        if configured_path:
            endpoints.append(configured_path if configured_path.startswith("/") else f"/{configured_path}")
        if "/health" not in endpoints:
            endpoints.append("/health")
        for fallback in ["/", "/ping"]:
            if fallback not in endpoints:
                endpoints.append(fallback)

        for ep in endpoints:
            url = f"{base}{ep}"
            try:
                r = await client.get(url, timeout=HEALTH_TIMEOUT)
                last_status = r.status_code
                if r.status_code in (200, 204):
                    is_up = True
                    break
            except Exception as e:
                details = f"Сеть / таймаут: {e}"

        if not is_up:
            # Не маскируем 404 как OK!
            if last_status == 404:
                details = f"HTTP 404 (endpoint '{endpoints[0]}' не найден)"
            elif last_status is not None:
                details = f"HTTP {last_status}"
    else:
        details = "Проверка по логам (URL не задан)"

    if is_up:
        if st.state != ServiceState.HEALTHY:
            st.state = ServiceState.HEALTHY
            st.failures = 0
            await notify(bot, f"🟢 *СЕРВИС ВОССТАНОВЛЕН*\n`{srv_name}` снова в строю!")
        return

    st.failures += 1
    st.last_error = details

    if st.failures == 2 and st.state != ServiceState.DEGRADED:
        st.state = ServiceState.DEGRADED
        await notify(bot, f"🚨 *ТРЕВОГА ПАТРУЛЯ*\nСервис `{srv_name}` недоступен!\nПричина: `{details}`\nСобираю логи с Render...")

        logs = get_service_logs(render_id, limit=50) if render_id else ""
        err_type = classify_logs(logs)

        if err_type == "TOKEN":
            st.state = ServiceState.WAITING_TOKEN
            await notify(bot, f"🔐 *ТРЕБУЕТСЯ КЛЮЧ*\nПроект: `{srv_name}`\nОтвалился API токен.\nПришли мне новый ключ в ответ.")
        elif err_type == "OOM":
            await notify(bot, f"💾 *ПАМЯТЬ ПЕРЕПОЛНЕНА (OOM)*\nПроект: `{srv_name}`\nПерезапускаю контейнер на Render...")
            if render_id:
                restart_service(render_id)
        elif err_type == "CODE_ERROR":
            await notify(bot, f"🛠 *ОШИБКА В КОДЕ*\nПроект: `{srv_name}`\nНайдена ошибка выполнения. Передаю задачу в Self-Heal...")
            # Реальный запуск Self-Heal с восстановлением!
            try:
                from engine.self_heal import attempt_self_heal
                heal_result = await asyncio.to_thread(
                    attempt_self_heal,
                    srv_name=srv_name,
                    render_id=render_id,
                    error_logs=logs,
                    bot=bot
                )
                if heal_result.get("success"):
                    st.state = ServiceState.HEALTHY
                    st.failures = 0
                    await notify(bot, (
                        f"✅ *SELF-HEAL: ПАТЧ УСПЕШНО ПРИМЕНЕН!*\n\n"
                        f"• Проект: `{srv_name}`\n"
                        f"• Исправлен файл: `{heal_result.get('target_file')}`\n"
                        f"• Репозиторий: {heal_result.get('repo_url')}\n"
                        f"• Контейнер: {'Перезапущен на Render' if heal_result.get('restarted') else 'Деплой запущен'}\n\n"
                        f"Сервис восстанавливается."
                    ))
                else:
                    reason = heal_result.get("reason", "Неизвестная ошибка")
                    await notify(bot, (
                        f"⚠️ *SELF-HEAL: Автовосстановление не удалось*\n\n"
                        f"• Проект: `{srv_name}`\n"
                        f"• Причина: `{reason}`\n"
                        f"Требуется ручная инспекция кода."
                    ))
            except Exception as exc:
                logger.error(f"[PATROL] Ошибка в процессе Self-Heal: {exc}")
                await notify(bot, f"❌ Сбой запуска Self-Heal для `{srv_name}`: {exc}")


async def check_registered_agents(client: httpx.AsyncClient, bot: Bot):
    agents = agent_registry.list_agents()
    for agent in agents:
        if not agent.health_url:
            continue
        try:
            r = await client.get(agent.health_url, timeout=HEALTH_TIMEOUT)
            if r.status_code == 200:
                if agent.status != AgentLifecycle.ACTIVE:
                    agent_registry.update_status(agent.agent_id, AgentLifecycle.ACTIVE, {"code": 200})
            else:
                agent_registry.update_status(agent.agent_id, AgentLifecycle.ERROR, {"code": r.status_code})
                await notify(bot, f"🚨 *СБОЙ АГЕНТА*\nАгент `{agent.name}` вернул HTTP {r.status_code}")
        except Exception as e:
            agent_registry.update_status(agent.agent_id, AgentLifecycle.ERROR, {"error": str(e)})


async def patrol_loop(bot: Bot):
    logger.info("Автономный патруль запущен. Мониторинг Render + Agent Registry активен.")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                brain_alive = check_self_brain()
                if not brain_alive:
                    logger.warning("[SELF-CHECK] Внимание: LLM провайдер Меты не отвечает!")

                try:
                    render_services = get_services()
                except Exception as e:
                    logger.error(f"Не удалось получить список сервисов Render: {e}")
                    render_services = []

                for item in render_services:
                    srv = item.get("service", {})
                    name = srv.get("name")
                    srv_id = srv.get("id")
                    url = srv.get("serviceDetails", {}).get("url") or ""
                    health_path = srv.get("serviceDetails", {}).get("healthCheckPath") or ""

                    if name and srv_id:
                        await check_single_service(
                            client=client,
                            bot=bot,
                            srv_name=name,
                            srv_url=url,
                            render_id=srv_id,
                            health_check_path=health_path
                        )
                        await asyncio.sleep(2)

                await check_registered_agents(client, bot)
                update_patrol_heartbeat(True)

            except Exception as e:
                logger.error(f"[PATROL EXCEPTION] Ошибка в итерации патруля: {e}")
                update_patrol_heartbeat(False, str(e))

            await asyncio.sleep(PATROL_INTERVAL)
