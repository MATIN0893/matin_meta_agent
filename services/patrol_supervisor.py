import time
import threading
import logging
from services.patrol_service import patrol_tick

try:
    from telegram import Bot
except ImportError:
    class Bot:
        pass

logger = logging.getLogger("MATIN.SUPERVISOR")

_supervisor_thread = None
_stop_event = threading.Event()

def _patrol_loop(bot: Bot):
    logger.info("[SUPERVISOR] Фоновый патруль SRE запущен (интервал: 15 минут).")
    while not _stop_event.is_set():
        try:
            logger.info("[SUPERVISOR] Запуск планового цикла патрулирования...")
            patrol_tick(bot)
        except Exception as e:
            logger.error(f"[SUPERVISOR] Сбой в цикле патрулирования: {e}")

        # Спим 15 минут (900 сек), проверяя флаг остановки каждые 5 сек
        for _ in range(180):
            if _stop_event.is_set():
                break
            time.sleep(5)

def start_supervised_patrol(bot: Bot):
    global _supervisor_thread
    if _supervisor_thread and _supervisor_thread.is_alive():
        return
    _stop_event.clear()
    _supervisor_thread = threading.Thread(
        target=_patrol_loop,
        args=(bot,),
        daemon=True,
        name="SRE-Patrol-Supervisor"
    )
    _supervisor_thread.start()
