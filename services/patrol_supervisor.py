import threading
import time
import asyncio
import logging
from telegram import Bot
from services.patrol_service import patrol_loop
from services.health_service import PATROL_STATS

logger = logging.getLogger("MATIN.SUPERVISOR")


def run_patrol_worker(bot: Bot):
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(patrol_loop(bot))
        except Exception as e:
            logger.critical(f"[SUPERVISOR] Patrol loop упал с фатальной ошибкой: {e}! Перезапуск через 10 сек...")
            PATROL_STATS["is_alive"] = False
            PATROL_STATS["last_error"] = str(e)
            time.sleep(10)


def start_supervised_patrol(bot: Bot) -> threading.Thread:
    t = threading.Thread(target=run_patrol_worker, args=(bot,), daemon=True, name="SRE-Patrol-Supervisor")
    t.start()
    logger.info("🛡 SRE Patrol Supervisor активирован.")
    return t
