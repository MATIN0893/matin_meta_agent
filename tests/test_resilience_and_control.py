import unittest
import asyncio
import os
import sys
import time
import json
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.task_engine import task_engine, TaskState, Task
from core.orchestrator import run_task, classify_task_intent
from core.llm_client import call_llm, ask
from bot.handlers.build_task import is_stop_intent, is_status_intent, handle_message


class TestResilienceAndControl(unittest.TestCase):

    def setUp(self):
        task_engine.release_lock()

    def tearDown(self):
        task_engine.release_lock()

    def test_01_task_lock_release_on_all_terminal_states(self):
        """Проверка освобождения task lock при COMPLETED, FAILED, TIMEOUT, CANCELLED, RECOVERED."""
        states = [
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.TIMEOUT,
            TaskState.CANCELLED,
            TaskState.RECOVERED
        ]
        for st in states:
            task = task_engine.create_task(user_id=1, command="test command")
            acquired = task_engine.acquire_lock(task)
            self.assertTrue(acquired)
            self.assertTrue(task_engine.is_locked())
            self.assertEqual(task_engine.get_active_task().task_id, task.task_id)

            task_engine.update_state(task.task_id, st, result="done")
            self.assertFalse(task_engine.is_locked(), f"Lock not released on state {st}")
            self.assertIsNone(task_engine.get_active_task())

    def test_02_stop_current_task_cancellation(self):
        """Проверка, что STOP/CANCEL останавливает активную задачу и освобождает lock."""
        task = task_engine.create_task(user_id=1, command="long task")
        task_engine.update_state(task.task_id, TaskState.EXECUTING, current_stage="[6/6 ANALYZE]")

        mock_async_task = MagicMock()
        mock_async_task.done.return_value = False
        task_engine.acquire_lock(task, mock_async_task)

        self.assertTrue(task_engine.is_locked())
        self.assertTrue(is_stop_intent("STOP"))
        self.assertTrue(is_stop_intent("STOP CURRENT TASK"))
        self.assertTrue(is_stop_intent("СТОП"))
        self.assertTrue(is_stop_intent("cancel"))

        cancelled = task_engine.cancel_current_task(reason="User stopped")
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.state, TaskState.CANCELLED)
        self.assertFalse(task_engine.is_locked())
        mock_async_task.cancel.assert_called_once()

    def test_03_status_check_does_not_launch_new_task(self):
        """Проверка, что STATUS CHECK перехватывается и не запускает новую диагностику."""
        task = task_engine.create_task(user_id=1, command="diagnose matin-agent")
        task.target_repo = "matin-agent"
        task.target_branch = "main"
        task_engine.update_state(task.task_id, TaskState.EXECUTING, current_stage="[6/6 ANALYZE]")
        task_engine.acquire_lock(task)

        self.assertTrue(is_status_intent("STATUS CHECK"))
        self.assertTrue(is_status_intent("статус"))
        self.assertTrue(is_status_intent("status"))

        active = task_engine.get_active_task()
        self.assertIsNotNone(active)
        self.assertEqual(active.current_stage, "[6/6 ANALYZE]")
        self.assertEqual(active.target_repo, "matin-agent")

    def test_04_target_repo_and_branch_persistence(self):
        """Проверка сохранения и извлечения контекста репозитория (target_repo, target_branch)."""
        task_engine.set_last_target_repo("MATIN0893/matin-agent", "main")
        self.assertEqual(task_engine.get_last_target_repo(), "MATIN0893/matin-agent")

        # Если команда не содержит имени репозитория, берется последний активный
        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_repo_tree") as mock_tree, \
             patch("services.github_service.get_repo_files") as mock_files:

            mock_ask.return_value = json.dumps({
                "action": "diagnostic",
                "task_type": "CODE_DIAGNOSTIC",
                "project_name": "", # пустое имя
                "target_file": "",
                "task_description": "подготовить план resilience",
                "env": {}
            })

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

            res = asyncio.run(run_task("подготовить технический план LLM resilience"))

            self.assertNotIn("400", res)
            self.assertNotIn("repository name empty", res)
            self.assertIn("matin-agent", res)

    def test_05_analyze_timeout_graceful_fallback(self):
        """Проверка: если LLM зависает в ANALYZE, срабатывает таймаут и возвращается детерминированный отчет."""
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
            mock_files.return_value = {"main.py": "import os\n# 429 Too Many Requests"}

            def slow_ask(system_prompt, user_prompt):
                if "Planner" in system_prompt:
                    return json.dumps({
                        "task_type": "CODE_DIAGNOSTIC",
                        "action": "diagnostic",
                        "project_name": "MATIN0893/matin-agent",
                        "target_file": "",
                        "search_queries": ["429"],
                        "task_description": "диагностика 429"
                    })
                time.sleep(2.0)
                return "Slow AI response"

            mock_ask.side_effect = slow_ask

            with patch.dict(os.environ, {"MATIN_ANALYZE_TIMEOUT": "0.5"}):
                res = asyncio.run(run_task("диагностика 429 в MATIN0893/matin-agent"))

                self.assertIn("ИНЖЕНЕРНАЯ ДИАГНОСТИКА", res)
                self.assertIn("Таймаут стадии глубокого LLM анализа", res)
                self.assertIn("Точки совпадений", res)
                self.assertIn("READ-ONLY", res)

    def test_06_no_save_files_error_in_read_and_stop_pipeline(self):
        """Проверка: отсутствие файлов для сохранения НЕ приводит к ошибке в READ/DIAGNOSTIC/STOP пайплайнах."""
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
            mock_files.return_value = {"main.py": "code"}

            mock_ask.return_value = json.dumps({
                "task_type": "REPOSITORY_INSPECTION",
                "action": "inspect",
                "project_name": "matin-agent",
                "target_file": "",
                "task_description": "инспекция"
            })

            res = asyncio.run(run_task("Покажи структуру matin-agent"))
            self.assertNotIn("Не удалось получить файлы для сохранения", res)

    def test_07_llm_client_timeout_enforced(self):
        """Проверка жесткого таймаута в call_llm при зависании сети/провайдера."""
        with patch("requests.post") as mock_post, \
             patch.dict(os.environ, {"GROQ_API_KEY": "dummy", "OPENROUTER_API_KEY": "dummy"}):

            def slow_post(*args, **kwargs):
                time.sleep(0.3)
                raise TimeoutError("Socket timeout")

            mock_post.side_effect = slow_post

            start_t = time.time()
            with self.assertRaises((TimeoutError, RuntimeError)):
                call_llm([{"role": "user", "content": "ping"}], timeout=0.2)
            elapsed = time.time() - start_t
            self.assertLess(elapsed, 2.0, "LLM timeout was not respected")


if __name__ == "__main__":
    unittest.main()
