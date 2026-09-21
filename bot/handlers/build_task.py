from telegram import Update
from telegram.ext import ContextTypes
from config.settings import ALLOWED_USERS
from core.orchestrator import run_task

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    msg = await update.message.reply_text("⚙️ Принял задачу, начинаю...")

    async def progress(step: str):
        await msg.edit_text(f"⚙️ {step}")

    result = await run_task(text, progress)
    await msg.edit_text(result)
