import asyncio
import re
from config.settings import is_user_allowed
from core.orchestrator import run_task
from core.security import security_guard
from engine.agent_factory import agent_factory
from engine.agent_registry import agent_registry
from services.github_service import (
    is_repo_list_intent,
    get_user_repositories,
    format_repositories_list,
)

try:
    from telegram import Update
    from telegram.ext import ContextTypes
except ImportError:
    class Update:
        pass
    class ContextTypes:
        DEFAULT_TYPE = None


def is_agent_list_intent(text: str) -> bool:
    t = text.strip().lower()
    return bool(re.search(r"^(покажи\s+(моих\s+)?агентов|список\s+агентов|мои\s+агенты|агенты|agents)$", t))


def is_create_agent_intent(text: str) -> bool:
    t = text.strip().lower()
    return bool(re.search(r"^(создай|сделай|разверни)\s+(нового\s+)?агента", t))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
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

    # 2. Интент списка агентов
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

    # 3. Интент создания нового агента (Agent Factory)
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
                report = (
                    f"✅ **Агент успешно создан!**\n\n"
                    f"• **Имя:** {res.get('name')}\n"
                    f"• **ID:** `{res.get('agent_id')}`\n"
                    f"• **Файлов сгенерировано:** {res.get('files_count')}\n"
                    f"• **Разрешения:** `{', '.join(res.get('permissions', []))}`\n"
                    f"• **Статус:** `READY` (все тесты успешно пройдены)\n\n"
                    f"Агент добавлен в реестр и готов к работе."
                )
                await msg.edit_text(report, parse_mode="Markdown")
            else:
                await msg.edit_text(f"❌ Ошибка создания агента: {res.get('error')}")
        except Exception as e:
            await msg.edit_text(f"❌ Фатальный сбой Agent Factory: {e}")
        return

    # 4. Интент удаления агента (Security Guard с подтверждением)
    del_match = re.search(r"^(?:удали|удалить)\s+агента(?:\s*:)?\s*[`'\"«]?([^`'\"»\n]+?)[`'\"»]?$", text, re.I)
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

    # 5. Интент списка репозиториев
    if is_repo_list_intent(text):
        msg = await update.message.reply_text("🔍 Запрашиваю список репозиториев с GitHub...")
        try:
            repos_data = await asyncio.to_thread(get_user_repositories)
            result = format_repositories_list(repos_data)
            await msg.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Ошибка при получении репозиториев: {e}")
        return

    # 6. Общая инженерная задача
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
