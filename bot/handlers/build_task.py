from telegram import Update
from telegram.ext import ContextTypes
from config.settings import is_user_allowed
from core.orchestrator import run_task


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
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

    try:
        result = await run_task(text, progress)
    except Exception as e:
        result = f"❌ Сбой выполнения задачи: {e}"

    try:
        await msg.edit_text(result, parse_mode="HTML")
    except Exception:
        try:
            await msg.edit_text(result)
        except Exception:
            pass
