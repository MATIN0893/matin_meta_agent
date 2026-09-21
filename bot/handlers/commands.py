from telegram import Update
from telegram.ext import ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Matin Meta Agent*\n\n"
        "Опиши что нужно создать — я напишу код, запушу на GitHub и задеплою на Render.\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/repos — мои проекты\n"
        "/status — статус последней задачи",
        parse_mode="Markdown"
    )

async def repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📁 Проекты: (скоро)")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = context.bot_data.get("current_task")
    if not task:
        await update.message.reply_text("Нет активных задач.")
        return
    await update.message.reply_text(
        f"📊 Статус: `{task.get('status', 'unknown')}`\n"
        f"Проект: `{task.get('project_name', '?')}`",
        parse_mode="Markdown"
    )
