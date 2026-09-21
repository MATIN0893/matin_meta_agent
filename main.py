from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from config.settings import TG_BOT_TOKEN
from bot.handlers.commands import start, repos, status
from bot.handlers.build_task import handle_message

def main():
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("repos", repos))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Meta Agent запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
