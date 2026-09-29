import asyncio
import os
import threading
import logging
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("MATIN.META")

TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")

from bot.handlers.commands import start, repos, status, agents_cmd, help_cmd, stop_cmd, cancel_cmd
from bot.handlers.build_task import handle_message
from services.health_service import run_health_server
from services.patrol_supervisor import start_supervised_patrol
from core.task_engine import task_engine


def run_dummy_server():
    run_health_server()


def run_patrol(bot):
    start_supervised_patrol(bot)


def main():
    threading.Thread(target=run_health_server, daemon=True, name="Health-Server").start()

    try:
        recovered = task_engine.recover_interrupted_tasks()
        if recovered:
            logger.info(f"Обнаружено и восстановлено {len(recovered)} прерванных задач.")
    except Exception as e:
        logger.error(f"Ошибка при восстановлении задач: {e}")

    if not TG_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN не задан! Бот не сможет запуститься.")
        while True:
            import time
            time.sleep(60)

    request = HTTPXRequest(connect_timeout=60, read_timeout=60)
    app = ApplicationBuilder().token(TG_BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("repos", repos))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("agents", agents_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    start_supervised_patrol(app.bot)

    logger.info("🤖 MATIN META Engineering OS запущена (SRE Patrol активен 24/7)")
    print("🤖 Meta Agent запущен (SRE Patrol активен)")

    app.run_polling(timeout=30)


if __name__ == "__main__":
    main()
