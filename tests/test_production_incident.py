import unittest
import asyncio
import os
import sys
import time
import json
from unittest.mock import patch, MagicMock, AsyncMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.task_engine import task_engine, TaskState, Task
from core.orchestrator import run_task, log_task_incident
from bot.handlers.build_task import (
    is_stop_intent,
    is_status_intent,
    chunk_message_text,
    safe_deliver_result,
    handle_message,
)
from bot.handlers.commands import status as status_cmd, stop_cmd


class TestProductionIncident(unittest.TestCase):

    def setUp(self):
        task_engine.release_lock()

    def tearDown(self):
        task_engine.release_lock()

    def test_01_safe_deliver_splits_long_message(self):
        """Проверка: сообщения > 3900 символов разбиваются на чанки и доставляются без 400 ошибки."""
        long_report = "ИНЖЕНЕРНЫЙ ОТЧЕТ:\n" + ("Строка анализа детального стека вызовов 429...\n" * 250)
        self.assertGreater(len(long_report), 8000)

        chunks = chunk_message_text(long_report, max_chunk_size=3900)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 3900)

        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_msg = MagicMock()
        mock_msg.edit_text = AsyncMock()

        asyncio.run(safe_deliver_result(mock_update, mock_msg, long_report))

        mock_msg.edit_text.assert_called_once()
        self.assertEqual(mock_update.message.reply_text.call_count, len(chunks) - 1)

    def test_02_safe_deliver_handles_entity_parse_error(self):
        """Проверка: если Markdown/HTML парсинг вызывает BadRequest, доставка переключается на plain-text."""
        bad_markdown = "Текст с незакрытыми тегами _*<invalid> и кодом"

        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_msg = MagicMock()

        mock_msg.edit_text = AsyncMock(side_effect=[Exception("Can't parse entities"), None])

        asyncio.run(safe_deliver_result(mock_update, mock_msg, bad_markdown))

        self.assertEqual(mock_msg.edit_text.call_count, 2)
        self.assertIsNone(mock_msg.edit_text.call_args_list[1].kwargs.get("parse_mode"))

    def test_03_status_control_routing_never_creates_task(self):
        """Проверка: STATUS CHECK, /status, статус не создают новую задачу в TaskEngine."""
        initial_tasks_count = len(task_engine.tasks)

        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_update.message.text = "STATUS CHECK"
        mock_update.message.reply_text = AsyncMock()

        mock_context = MagicMock()

        with patch("bot.handlers.commands.status", new_callable=AsyncMock) as mock_st:
            asyncio.run(handle_message(mock_update, mock_context))
            mock_st.assert_called_once()
            self.assertEqual(len(task_engine.tasks), initial_tasks_count)

        task = task_engine.create_task(user_id=12345, command="smoke test")
        task.target_repo = "matin-agent"
        task_engine.update_state(task.task_id, TaskState.EXECUTING, current_stage="[6/6 ANALYZE]")
        task_engine.acquire_lock(task)

        mock_update.message.text = "status check."
        asyncio.run(handle_message(mock_update, mock_context))

        mock_update.message.reply_text.assert_called()
        call_text = mock_update.message.reply_text.call_args[0][0]
        self.assertIn("ТЕКУЩАЯ ЗАДАЧА В ПРОЦЕССЕ ВЫПОЛНЕНИЯ", call_text)
        self.assertIn("[6/6 ANALYZE]", call_text)
        self.assertIn("matin-agent", call_text)

    def test_04_stop_control_routing_cancels_and_releases_lock(self):
        """Проверка: STOP CURRENT TASK, /stop прерывает задачу и немедленно освобождает лок."""
        task = task_engine.create_task(user_id=12345, command="long diagnostic")
        task_engine.update_state(task.task_id, TaskState.EXECUTING, current_stage="[6/6 ANALYZE]")

        mock_async_task = MagicMock()
        mock_async_task.done.return_value = False
        task_engine.acquire_lock(task, mock_async_task)
        self.assertTrue(task_engine.is_locked())

        mock_update = MagicMock()
        mock_update.effective_user.id = 12345
        mock_update.message.text = "STOP CURRENT TASK"
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        asyncio.run(handle_message(mock_update, mock_context))

        self.assertFalse(task_engine.is_locked())
        cancelled_task = task_engine.get_task(task.task_id)
        self.assertEqual(cancelled_task.state, TaskState.CANCELLED)
        mock_async_task.cancel.assert_called_once()

    def test_05_incident_logging_contains_required_fields_and_no_secrets(self):
        """Проверка: log_task_incident выводит все требуемые поля инцидента без утечки секретов."""
        task = task_engine.create_task(user_id=999, command="diagnose MATIN0893/matin-agent")
        task.target_repo = "MATIN0893/matin-agent"
        task.target_branch = "main"
        task.task_type = "CODE_DIAGNOSTIC"
        task.current_stage = "[6/6 ANALYZE]"

        with patch("core.orchestrator.logger.warning") as mock_log:
            log_task_incident(
                task,
                reason="Тестовый таймаут стадии ANALYZE",
                timeout=45.0,
                llm_model="openai/gpt-oss-120b"
            )
            mock_log.assert_called_once()
            log_output = mock_log.call_args[0][0]

            self.assertIn("TASK_ID:", log_output)
            self.assertIn("USER_ID:       999", log_output)
            self.assertIn("TARGET_REPO:   MATIN0893/matin-agent", log_output)
            self.assertIn("TASK_TYPE:     CODE_DIAGNOSTIC", log_output)
            self.assertIn("CURRENT_STAGE: [6/6 ANALYZE]", log_output)
            self.assertIn("START_TIME:", log_output)
            self.assertIn("LLM_MODEL:     openai/gpt-oss-120b", log_output)
            self.assertIn("TIMEOUT:       45.0s", log_output)
            self.assertIn("TASK_STATE:", log_output)
            self.assertIn("LOCK_STATE:", log_output)
            self.assertIn("REASON:        Тестовый таймаут стадии ANALYZE", log_output)

            self.assertNotIn("ghp_", log_output)
            self.assertNotIn("rnd_", log_output)
            self.assertNotIn("gsk_", log_output)

    def test_06_real_smoke_test_full_lifecycle(self):
        """
        Реальный безопасный SMOKE TEST полного жизненного цикла:
        1. Создание задачи -> смена стадий 1..6
        2. STATUS CHECK во время работы -> возврат активной задачи без новой
        3. STOP CURRENT TASK во время работы -> перевод в CANCELLED и снятие лока
        4. Запуск второй задачи -> успешное завершение и сохранение target_repo
        """
        task1 = task_engine.create_task(user_id=123, command="smoke test MATIN0893/matin-agent")
        task1.target_repo = "MATIN0893/matin-agent"
        task_engine.acquire_lock(task1)

        stages = ["[1/6 UNDERSTAND]", "[2/6 REPOSITORY_CONTEXT]", "[3/6 READ_FILES]", "[4/6 SEARCH]", "[5/6 TRACE]", "[6/6 ANALYZE]"]
        for st in stages:
            task_engine.update_state(task1.task_id, TaskState.EXECUTING, current_stage=st)
            self.assertEqual(task_engine.get_active_task().current_stage, st)

        mock_update = MagicMock()
        mock_update.effective_user.id = 123
        mock_update.message.text = "STATUS CHECK"
        mock_update.message.reply_text = AsyncMock()

        asyncio.run(handle_message(mock_update, MagicMock()))
        status_reply = mock_update.message.reply_text.call_args[0][0]
        self.assertIn("[6/6 ANALYZE]", status_reply)
        self.assertIn("MATIN0893/matin-agent", status_reply)
        self.assertTrue(task_engine.is_locked())

        mock_update.message.text = "STOP CURRENT TASK"
        asyncio.run(handle_message(mock_update, MagicMock()))
        self.assertFalse(task_engine.is_locked())
        self.assertEqual(task_engine.get_task(task1.task_id).state, TaskState.CANCELLED)

        self.assertEqual(task_engine.get_last_target_repo(), "MATIN0893/matin-agent")

        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_repo_tree") as mock_tree, \
             patch("services.github_service.get_repo_files") as mock_files:

            mock_meta.return_value = {
                "success": True,
                "name": "matin-agent",
                "full_name": "MATIN0893/matin-agent",
                "default_branch": "main",
                "private": True,
                "html_url": "https://github.com/MATIN0893/matin-agent",
                "status_code": 200
            }
            mock_tree.return_value = {"success": True, "branch": "main", "files": ["main.py"]}
            mock_files.return_value = {"main.py": "print('ok')"}
            mock_ask.side_effect = [
                json.dumps({
                    "task_type": "CODE_DIAGNOSTIC",
                    "action": "diagnostic",
                    "project_name": "",
                    "task_description": "диагностика"
                }),
                "Диагностический отчет: все модули в норме."
            ]

            res = asyncio.run(run_task("проведи диагностику"))
            self.assertIn("ИНЖЕНЕРНАЯ ДИАГНОСТИКА", res)
            self.assertIn("matin-agent", res)
            self.assertFalse(task_engine.is_locked())


if __name__ == "__main__":
    unittest.main()
