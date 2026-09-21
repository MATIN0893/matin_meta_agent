from telegram import Update
from telegram.ext import ContextTypes
from services.github_service import list_user_repos

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Matin Meta Agent*\n\n"
        "Опиши задачу текстом:\n"
        "— Создать новый проект с нуля\n"
        "— Либо изменить существующий проект\n\n"
        "Команды:\n"
        "/start — справка\n"
        "/repos — список твоих репозиториев на GitHub\n"
        "/status — статус последней задачи",
        parse_mode="Markdown"
    )

async def repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Загружаю список репозиториев...")
    try:
        repo_list = list_user_repos()
        if not repo_list:
            await msg.edit_text("Репозитории не найдены.")
            return

        formatted = "\n".join([f"• `{name}`" for name in repo_list])
        await msg.edit_text(
            f"📁 *Твои репозитории:*\n\n{formatted}\n\n"
            f"Чтобы изменить проект, отправь задачу вида:\n"
            f"_В проекте имя_репозитория добавь/исправь..._",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка GitHub: {e}")

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