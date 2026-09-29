import asyncio
import os
import json
import time
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import is_user_allowed
from services.github_service import (
    get_user_repositories,
    format_repositories_list,
)
from services.patrol_service import STATE, check_self_brain, ServiceState
from services.health_service import PATROL_STATS, get_full_health_report
from services.render_service import get_services
from engine.agent_registry import agent_registry
from core.task_engine import task_engine, TaskState


def is_allowed(user_id: int) -> bool:
    return is_user_allowed(user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    welcome_text = (
        "🤖 **MATIN META — Autonomous Engineering OS 2.0**\n\n"
        "Автономная операционная система для управления проектами, кодом, облачной инфраструктурой и созданием AI-агентов 24/7.\n\n"
        "⚡ **Возможности системы:**\n"
        "• 🏭 **Agent Factory:** создание новых AI-агентов с нуля по описанию\n"
        "• 📁 **GitHub Engine:** инспекция, нечеткий поиск, коммиты\n"
        "• 📡 **Render Engine:** автодеплой, сбор логов, перезапуск\n"
        "• 🛡 **SRE Patrol 2.0:** непрерывный патруль и self-healing каждые 15 мин\n"
        "• 🔒 **Security Guard:** защита от деструктивных действий и утечек токенов\n\n"
        "📌 **Основные команды:**\n"
        "• /status — Полный рапорт здоровья системы и сервисов\n"
        "• /stop — Немедленно остановить текущую выполняющуюся задачу\n"
        "• /agents — Список созданных AI-агентов в реестре\n"
        "• /repos — Список репозиториев на GitHub\n"
        "• /help — Инструкция по управлению на естественном языке"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    active_block = ""
    active = task_engine.get_active_task()
    if active:
        elapsed = int(time.time() - active.created_at)
        active_block = (
            "⚙️ **ТЕКУЩАЯ ЗАДАЧА В ПРОЦЕССЕ ВЫПОЛНЕНИЯ:**\n"
            f"• **ID:** `{active.task_id}`\n"
            f"• **Состояние:** `{active.state.value}`\n"
            f"• **Стадия:** {active.current_stage or 'Выполняется...'}\n"
            f"• **Репозиторий:** `{active.target_repo or '—'}` (`{active.target_branch}`)\n"
            f"• **Время работы:** `{elapsed} сек`\n"
            f"• **Команда:** _{active.command}_\n"
            "💡 _Отправь `СТОП` или `/stop` для отмены_\n\n"
            "────────────────────────────────────────\n"
        )

    brain_ok = await asyncio.to_thread(check_self_brain)
    brain_status = "🟢 В норме (LLM Router активен)" if brain_ok else "🔴 Ошибка связи с LLM"

    try:
        render_list = get_services()
    except Exception:
        render_list = []

    services_lines = []
    for item in render_list:
        srv = item.get("service", {})
        name = srv.get("name", "unknown")
        runtime = STATE.get(name)

        if not runtime or runtime.state == ServiceState.HEALTHY:
            icon = "🟢"
            state_text = "online"
        elif runtime.state == ServiceState.WAITING_TOKEN:
            icon = "🔐"
            state_text = "waiting token"
        else:
            icon = "🚨"
            state_text = f"degraded ({runtime.last_error[:20]})"

        services_lines.append(f"{icon} `{name}` — {state_text}")

    services_block = "\n".join(services_lines) if services_lines else "• Нет данных"

    patrol_status = "🟢 Активен" if PATROL_STATS.get("is_alive") else "🟡 Ожидает первого цикла"
    patrol_runs = PATROL_STATS.get("total_runs", 0)
    agents_count = len(agent_registry.list_agents())

    status_msg = (
        f"{active_block}"
        "🛡 **SRE CONTROL PLANE: СТАТУС**\n\n"
        f"• 🧠 **Мозг агента:** {brain_status}\n"
        f"• ⏱ **SRE Patrol:** {patrol_status} (циклов: {patrol_runs})\n"
        f"• ☁️ **Облако 24/7:** Независим от ПК (Render Web)\n"
        f"• 🏭 **Зарегистрировано агентов:** {agents_count}\n"
        f"• 📡 **Сервисов под надзором Render:** {len(render_list)}\n\n"
        "**Инфраструктура:**\n"
        f"{services_block}\n\n"
        "🚀 _Автопатруль и Self-Heal активны в фоновом режиме_"
    )
    await update.message.reply_text(status_msg, parse_mode="Markdown")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    active = task_engine.get_active_task()
    if active:
        cancelled = task_engine.cancel_current_task(reason="Остановлено по команде /stop")
        cid = cancelled.task_id if cancelled else active.task_id
        cmd = cancelled.command if cancelled else active.command
        resp = (
            "🛑 **Текущая задача успешно остановлена**\n\n"
            f"• **ID:** `{cid}`\n"
            f"• **Команда:** _{cmd}_\n"
            "• **Состояние:** `CANCELLED`\n"
            "• **Task Lock:** Освобожден ✅\n\n"
            "Система разблокирована и готова к приему новых команд."
        )
        await update.message.reply_text(resp, parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ В данный момент нет активных выполняющихся задач.", parse_mode="Markdown")


async def repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    await update.message.reply_text("🔍 Запрашиваю список репозиториев с GitHub...")
    try:
        repos_data = await asyncio.to_thread(get_user_repositories)
        if not repos_data:
            await update.message.reply_text("📁 У тебя пока нет доступных репозиториев или не настроен токен GitHub.")
            return

        text = format_repositories_list(repos_data)
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000], parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при получении репозиториев: {e}")


async def agents_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    agents = agent_registry.list_agents()
    if not agents:
        await update.message.reply_text(
            "📁 В реестре пока нет созданных агентов.\n\n"
            "Чтобы создать нового, напиши например:\n"
            "👉 `Создай нового агента MATIN MONITOR для проверки ботов`"
        )
        return

    lines = ["🏭 **Зарегистрированные AI-агенты:**\n"]
    for a in agents:
        status_icon = "🟢" if a.status.value in ("active", "ready") else "🟡"
        perms = ", ".join(a.permissions) if a.permissions else "standard"
        lines.append(f"{status_icon} **{a.name}** (`{a.agent_id}`) v{a.version}\n   • Статус: `{a.status.value}`\n   • Доступ: `{perms}`\n   • Репо: `{a.github_repo}`\n")

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return

    help_text = (
        "📖 **Справка по управлению MATIN META:**\n\n"
        "Ты можешь отправлять любые инженерные задачи обычным текстом:\n\n"
        "• **Управление активной задачей:**\n"
        "  `СТАТУС` или `STATUS CHECK` — узнать, на какой стадии находится текущая задача\n"
        "  `СТОП` или `STOP CURRENT TASK` — немедленно прервать текущую задачу\n\n"
        "• **Создание агентов:**\n"
        "  `Создай агента MATIN MONITOR`\n"
        "  `Создай агента для мониторинга Telegram-ботов`\n\n"
        "• **Управление агентами:**\n"
        "  `Покажи моих агентов`\n"
        "  `Проверь агента matin-monitor`\n"
        "  `Удали агента matin-monitor` (запросит подтверждение)\n\n"
        "• **Инфраструктура и проекты:**\n"
        "  `Список репозиториев`\n"
        "  `Проверь matin-agent`\n"
        "  `Найди источник ошибки 429 в MATIN0893/matin-agent`\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


cancel_cmd = stop_cmd
repos_cmd = repos
status_cmd = status
start_cmd = start
