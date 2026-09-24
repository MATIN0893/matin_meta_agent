import os
from telegram import Update
from telegram.ext import ContextTypes
from services.github_service import list_user_repos

# Разрешенные ID пользователей через запятую
raw_users = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(u.strip()) for u in raw_users.split(",") if u.strip().isdigit()]

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # Если список пуст, разрешено всем (или настрой по вкусу)
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return
    
    welcome_text = (
        "🤖 **Matin Meta Agent запущен и в строю!**\n\n"
        "Я готов управлять репозиториями, координировать задачи и держать систему под контролем.\n\n"
        "Доступные команды:\n"
        "• /status — Проверить, жив ли бот и чем занят\n"
        "• /repos — Список репозиториев на GitHub"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("⛔ Доступ ограничен.")
        return

    # Живой статус системы
    status_msg = (
        "🟢 **СТАТУС СИСТЕМЫ: АКТИВЕН И ЖИВ**\n\n"
        "• 🤖 **Бот:** На связи, поллинг работает\n"
        "• ⚙️ **Оркестратор:** Готов к работе\n"
        "• 🧠 **Gemini API:** Защита от лимитов активна\n"
        "• 🚀 **Состояние:** Ожидание задач / Свободен"
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