import asyncio
import os
import json
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
        "  `Проверь Render`\n"
        "  `Исправь ошибку в main.py репозитория X`\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


repos_cmd = repos
status_cmd = status
start_cmd = start
