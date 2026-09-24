from telegram import Update
from telegram.ext import ContextTypes
from config.settings import ALLOWED_USERS
from core.orchestrator import run_task

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) not in str(ALLOWED_USERS):
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    msg = await update.message.reply_text("⚙️ Принял задачу, начинаю...")

    async def progress(step: str):
        try:
            await msg.edit_text(f"⚙️ {step}", parse_mode="HTML")
        except Exception:
            pass

    result = await run_task(text, progress)
    try:
        await msg.edit_text(result, parse_mode="HTML")
    except Exception:
        await msg.edit_text(result)
