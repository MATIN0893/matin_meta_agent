import os
from telegram import Update
from telegram.ext import ContextTypes
from services.github_service import list_user_repos
from services.patrol_service import STATE, check_self_brain, ServiceState
from services.render_service import get_services

# Разрешенные ID пользователей через запятую
raw_users = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(u.strip()) for u in raw_users.split(",") if u.strip().isdigit()]

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    welcome_text = (
        "🤖 **Matin Meta Agent (SRE Control Plane)**\n\n"
        "Система автономного мониторинга, самодиагностики и восстановления активна 24/7.\n\n"
        "Команды:\n"
        "• /status — Полный рапорт SRE Patrol и здоровье сервисов\n"
        "• /repos — Список репозиториев на GitHub"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    brain_ok = check_self_brain()
    brain_status = "🟢 В норме (LLM Router активен)" if brain_ok else "🔴 Ошибка связи с LLM"

    # Получаем актуальный список сервисов
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

    status_msg = (
        "🛡 **SRE CONTROL PLANE: СТАТУС**\n\n"
        f"• 🧠 **Мозг агента:** {brain_status}\n"
        "• ⚙️ **Self-Heal Engine:** AST Guard (порог >= 0.90)\n"
        f"• 📡 **Сервисов под надзором:** {len(render_list)}\n\n"
        "**Инфраструктура Render:**\n"
        f"{services_block}\n\n"
        "🚀 _Автопатруль активен в фоновом режиме_"
    )
    await update.message.reply_text(status_msg, parse_mode="Markdown")

async def repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    await update.message.reply_text("🔍 Запрашиваю список репозиториев с GitHub...")
    try:
        repos_list = list_user_repos()
        if not repos_list:
            await update.message.reply_text("📁 У тебя пока нет доступных репозиториев или не настроен токен GitHub.")
            return

        text = "📁 **Твои репозитории на GitHub:**\n\n" + "\n".join([f"• `{r}`" for r in repos_list[:15]])
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при получении репозиториев: {e}")
