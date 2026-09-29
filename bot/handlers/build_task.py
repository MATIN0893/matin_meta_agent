import asyncio
import os
import re
import time
import logging
from config.settings import is_user_allowed
from core.orchestrator import run_task, log_task_incident
from core.security import security_guard
from core.task_engine import task_engine, TaskState
from engine.agent_factory import agent_factory
from engine.agent_registry import agent_registry
from services.github_service import (
    is_repo_list_intent,
    get_user_repositories,
    format_repositories_list,
)

logger = logging.getLogger("MATIN.BUILD_TASK")

try:
    from telegram import Update
    from telegram.error import BadRequest, TelegramError
    from telegram.ext import ContextTypes
except ImportError:
    class Update:
        pass
    class ContextTypes:
        DEFAULT_TYPE = None
    class TelegramError(Exception):
        pass
    class BadRequest(TelegramError):
        pass


def sanitize_cmd_text(text: str) -> str:
    """Нормализует текст команды: убирает слэш, username бота, лишние пробелы и знаки препинания."""
    t = text.strip()
    t = re.sub(r"^/([a-zA-Z0-9_]+)(?:@[a-zA-Z0-9_]+)?", r"\1", t)
    t = re.sub(r"[.!?,]+$", "", t).strip().lower()
    return t


def is_stop_intent(text: str) -> bool:
    """Точный и исчерпывающий перехват всех команд остановки/отмены задачи."""
    t = sanitize_cmd_text(text)
    patterns = [
        r"^(?:stop|cancel|halt|kill)$",
        r"^(?:стоп|останови|остановить|прерви|прервать|отмена|отмени)$",
        r"^(?:stop|cancel)\s+(?:task|current\s+task|job|operation|execution)$",
        r"^(?:останови|остановить|прерви|прервать|отмени|отменить)\s+(?:задачу|текущую\s+задачу|процесс|операцию)$",
    ]
    return any(bool(re.search(p, t, re.I)) for p in patterns)


def is_status_intent(text: str) -> bool:
    """Точный и исчерпывающий перехват всех команд запроса статуса/состояния."""
    t = sanitize_cmd_text(text)
    patterns = [
        r"^(?:status|state|health|info)$",
        r"^(?:статус|состояние|инфо)$",
        r"^(?:status\s+check|check\s+status|status\s+report|system\s+status)$",
        r"^(?:проверь\s+статус|статус\s+задачи|статус\s+проекта|текущий\s+статус|статус\s+системы)$",
    ]
    return any(bool(re.search(p, t, re.I)) for p in patterns)


def is_agent_list_intent(text: str) -> bool:
    t = sanitize_cmd_text(text)
    return bool(re.search(r"^(покажи\s+(моих\s+)?агентов|список\s+агентов|мои\s+агенты|агенты|agents)$", t))


def is_create_agent_intent(text: str) -> bool:
    t = text.strip().lower()
    return bool(re.search(r"^(создай|сделай|разверни)\s+(нового\s+)?агента", t))


def chunk_message_text(text: str, max_chunk_size: int = 3900) -> list:
    """Безопасно разбивает длинное сообщение на блоки до 3900 символов (лимит Telegram 4096)."""
    if not text:
        return [""]
    if len(text) <= max_chunk_size:
        return [text]

    chunks = []
    current = []
    current_len = 0

    lines = text.split("\n")
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chunk_size:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            while len(line) > max_chunk_size:
                chunks.append(line[:max_chunk_size])
                line = line[max_chunk_size:]
            current.append(line)
            current_len = len(line) + 1
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


async def safe_deliver_result(update: Update, progress_msg, result_text: str):
    """
    Безопасная доставка финального ответа без editMessageText 400 Bad Request:
    1. Автоматически разбивает ответ > 3900 символов на несколько сообщений.
    2. Первый блок редактирует progress_msg, последующие отправляет как reply_text.
    3. При ошибке парсинга разметки (Markdown/HTML entities) мгновенно откатывается на plain-text.
    4. При любой ошибке editMessageText гарантированно отправляет reply_text.
    """
    chunks = chunk_message_text(result_text, max_chunk_size=3900)
    first_chunk = chunks[0] if chunks else "✅ Задача выполнена."

    edited = False
    if progress_msg and hasattr(progress_msg, "edit_text"):
        for mode in ["Markdown", None]:
            try:
                await progress_msg.edit_text(first_chunk, parse_mode=mode)
                edited = True
                break
            except Exception as e:
                err_str = str(e)
                if "Message is not modified" in err_str:
                    edited = True
                    break
                logger.warning(f"[DELIVERY] Ошибка edit_text (mode={mode}): {e}")

    if not edited:
        try:
            await update.message.reply_text(first_chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(first_chunk)

    for extra_chunk in chunks[1:]:
        try:
            await update.message.reply_text(extra_chunk, parse_mode="Markdown")
        except Exception:
            try:
                await update.message.reply_text(extra_chunk)
            except Exception as e:
                logger.error(f"[DELIVERY] Не удалось отправить дополнительный фрагмент: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    text = update.message.text.strip()
    if not text:
        return

    # 1. Проверка подтверждения деструктивных действий (Security Guard)
    is_confirmed, pending = security_guard.check_confirmation(user_id, text)
    if is_confirmed and pending:
        act = pending.get("action")
        target = pending.get("target")
        msg = await update.message.reply_text(f"⏳ Подтверждение принято. Выполняю `{act}` для `{target}`...")
        if act == "delete_agent":
            ok = agent_registry.delete_agent(target)
            if ok:
                await msg.edit_text(f"✅ Агент `{target}` успешно удален из реестра и с диска.")
            else:
                await msg.edit_text(f"❌ Агент `{target}` не найден в реестре.")
        return

    # 2. ПЕРЕХВАТ CONTROL-КОМАНДЫ: STOP / STOP CURRENT TASK / CANCEL
    if is_stop_intent(text):
        active = task_engine.get_active_task()
        if active:
            cancelled = task_engine.cancel_current_task(reason="Остановлено по команде пользователя")
            cid = cancelled.task_id if cancelled else active.task_id
            cmd = cancelled.command if cancelled else active.command
            log_task_incident(cancelled or active, reason="Ручная остановка пользователем (STOP CURRENT TASK)")
            resp = (
                "🛑 **Текущая задача остановлена**\n\n"
                f"• **ID:** `{cid}`\n"
                f"• **Команда:** _{cmd}_\n"
                "• **Состояние:** `CANCELLED`\n"
                "• **Task Lock:** Освобожден ✅\n\n"
                "Система разблокирована и готова к приему новых команд."
            )
            await update.message.reply_text(resp, parse_mode="Markdown")
        else:
            await update.message.reply_text("ℹ️ В данный момент нет активных выполняющихся задач.", parse_mode="Markdown")
        return

    # 3. ПЕРЕХВАТ CONTROL-КОМАНДЫ: STATUS / STATUS CHECK
    if is_status_intent(text):
        active = task_engine.get_active_task()
        if active:
            elapsed = int(time.time() - active.created_at)
            stage = active.current_stage or "Выполняется..."
            repo_info = f"`{active.target_repo}` ({active.target_branch})" if active.target_repo else "не определен"
            resp = (
                "⚙️ **ТЕКУЩАЯ ЗАДАЧА В ПРОЦЕССЕ ВЫПОЛНЕНИЯ**\n\n"
                f"• **ID задачи:** `{active.task_id}`\n"
                f"• **Состояние:** `{active.state.value}`\n"
                f"• **Текущая стадия:** {stage}\n"
                f"• **Целевой репозиторий:** {repo_info}\n"
                f"• **Время работы:** `{elapsed} сек`\n"
                f"• **Команда:** _{active.command}_\n\n"
                "💡 _Чтобы остановить задачу, отправь `СТОП` или `/stop`._"
            )
            await update.message.reply_text(resp, parse_mode="Markdown")
        else:
            from bot.handlers.commands import status as status_cmd
            await status_cmd(update, context)
        return

    # Если сообщение начинается со слэша, но не перехвачено выше — пропускаем для CommandHandler
    if text.startswith("/"):
        return

    # 4. Интент списка агентов
    if is_agent_list_intent(text):
        agents = agent_registry.list_agents()
        if not agents:
            await update.message.reply_text("📁 В реестре пока нет агентов. Напиши: `Создай агента ...`")
            return
        lines = ["🏭 **Зарегистрированные AI-агенты:**\n"]
        for a in agents:
            status_icon = "🟢" if a.status.value in ("active", "ready") else "🟡"
            lines.append(f"{status_icon} **{a.name}** (`{a.agent_id}`) v{a.version}\n   • Статус: `{a.status.value}`\n")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # 5. Интент создания нового агента (Agent Factory)
    if is_create_agent_intent(text):
        msg = await update.message.reply_text("🏭 **Agent Factory запущена**\nАнализирую требования к агенту...")

        def factory_cb(step_msg: str):
            try:
                context.application.create_task(msg.edit_text(f"🏭 {step_msg}"))
            except Exception:
                pass

        try:
            res = await asyncio.to_thread(agent_factory.create_agent, text, factory_cb)
            if res.get("success"):
                perms_list = res.get('permissions', [])
                perms_str = ', '.join(perms_list) if perms_list else 'standard'
                report = (
                    f"✅ **Агент успешно создан!**\n\n"
                    f"• **Имя:** {res.get('name')}\n"
                    f"• **ID:** `{res.get('agent_id')}`\n"
                    f"• **Файлов сгенерировано:** {res.get('files_count')}\n"
                    f"• **Разрешения:** `{perms_str}`\n"
                    f"• **Статус:** `READY` (все тесты успешно пройдены)\n\n"
                    f"Агент добавлен в реестр и готов к работе."
                )
                await msg.edit_text(report, parse_mode="Markdown")
            else:
                await msg.edit_text(f"❌ Ошибка создания агента: {res.get('error')}")
        except Exception as e:
            await msg.edit_text(f"❌ Фатальный сбой Agent Factory: {e}")
        return

    # 6. Интент удаления агента (Security Guard с подтверждением)
    del_match = re.search(r"^(?:удали|удалить)\s+агента(?:\\s*:)?\s*([a-zA-Z0-9_\-]+)$", text, re.I)
    if del_match:
        target_agent = del_match.group(1).strip()
        existing = agent_registry.find_agent(target_agent)
        aid_to_del = existing.agent_id if existing else target_agent
        display_name = existing.name if existing else target_agent

        warn_msg = security_guard.create_confirmation_request(
            user_id=user_id,
            action="delete_agent",
            target=aid_to_del,
            payload={"agent_id": aid_to_del, "name": display_name}
        )
        await update.message.reply_text(warn_msg, parse_mode="Markdown")
        return

    # 7. Интент списка репозиториев
    if is_repo_list_intent(text):
        msg = await update.message.reply_text("🔍 Запрашиваю список репозиториев с GitHub...")
        try:
            repos_data = await asyncio.to_thread(get_user_repositories)
            result = format_repositories_list(repos_data)
            await msg.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка при получении репозиториев: {e}")
        return

    # 8. ПРОВЕРКА TASK LOCK: если задача уже выполняется, блокируем запуск второй
    if task_engine.is_locked():
        active = task_engine.get_active_task()
        active_id = active.task_id if active else "unknown"
        active_cmd = active.command if active else "задача"
        stage = active.current_stage if active else "выполняется"
        await update.message.reply_text(
            f"⚠️ **Внимание: уже выполняется задача!**\n\n"
            f"• **ID задачи:** `{active_id}`\n"
            f"• **Текущая стадия:** {stage}\n"
            f"• **Команда:** _{active_cmd}_\n\n"
            f"Пожалуйста, дождитесь ее завершения или отправьте `СТОП` для отмены.",
            parse_mode="Markdown"
        )
        return

    # 9. СОЗДАНИЕ ЗАДАЧИ И ЗАХВАТ TASK LOCK СРАЗУ
    task = task_engine.create_task(user_id=user_id, command=text)
    msg = await update.message.reply_text("⚙️ Принял задачу, начинаю...")

    last_progress_text = ""

    async def progress(step: str):
        nonlocal last_progress_text
        if step == last_progress_text:
            return
        last_progress_text = step
        try:
            await msg.edit_text(f"⚙️ {step}")
        except Exception as e:
            err_msg = str(e)
            if "Message is not modified" not in err_msg:
                logger.debug(f"[PROGRESS] edit_text exception: {e}")

    TASK_TIMEOUT = float(os.getenv("MATIN_TASK_TIMEOUT", "180.0"))
    task_coro = run_task(text, progress, existing_task=task)
    running_async_task = asyncio.create_task(task_coro)
    task_engine.acquire_lock(task, running_async_task)

    try:
        result = await asyncio.wait_for(running_async_task, timeout=TASK_TIMEOUT)
    except asyncio.CancelledError:
        result = "🛑 Задача была прервана пользователем."
        task_engine.cancel_current_task(reason="Отменено пользователем")
    except asyncio.TimeoutError:
        task_engine.cancel_current_task(reason=f"Превышен общий таймаут ({TASK_TIMEOUT}s)")
        log_task_incident(task, reason=f"Превышен общий task timeout ({TASK_TIMEOUT}s)", timeout=TASK_TIMEOUT)
        result = f"⏱ **Превышен общий таймаут выполнения задачи ({TASK_TIMEOUT} сек).** Задача остановлена."
    except Exception as e:
        log_task_incident(task, reason=f"Необработанный сбой: {e}")
        result = f"❌ Сбой выполнения задачи: {e}"

    # 10. Безопасная доставка финального ответа без editMessageText 400
    await safe_deliver_result(update, msg, result)
