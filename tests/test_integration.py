import unittest
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.orchestrator import collect_project_files, safe_parse_json
from core.security import security_guard, Permission
from core.task_engine import task_engine, TaskState
from core.memory import memory
from engine.agent_registry import agent_registry, AgentLifecycle
from engine.agent_factory import agent_factory
from engine.self_heal import attempt_self_heal, extract_failing_file
from services.health_service import get_full_health_report, check_github_status


class TestIntegration(unittest.TestCase):

    def test_full_agent_lifecycle(self):
        """Интеграционный тест: создание агента, регистрация, удаление через подтверждение."""
        # 1. Создание через фабрику
        agent_id = "matin-int-test"
        spec = agent_factory._generate_builtin_template(agent_id, "Интеграционный агент")
        self.assertEqual(spec["agent_id"], agent_id)

        # 2. Регистрация в реестре
        record = agent_registry.register_agent(
            agent_id=agent_id,
            name=spec["name"],
            description=spec["description"],
            permissions=spec["permissions"]
        )
        self.assertEqual(record.status, AgentLifecycle.CONFIGURED)
        agent_registry.update_status(agent_id, AgentLifecycle.READY)

        # 3. Поиск агента разными способами
        found_exact = agent_registry.find_agent(agent_id)
        self.assertIsNotNone(found_exact)
        found_name = agent_registry.find_agent(spec["name"])
        self.assertIsNotNone(found_name)

        # 4. Запрос на удаление через Security Guard
        user_id = 12345
        req_msg = security_guard.create_confirmation_request(user_id, "delete_agent", agent_id)
        self.assertIn("ПОДТВЕРЖДАЮ", req_msg)

        # 5. Подтверждение и удаление
        pending = security_guard.pending_confirmations[user_id]
        code = pending["code"]
        confirmed, req_data = security_guard.check_confirmation(user_id, f"ПОДТВЕРЖДАЮ {code}")
        self.assertTrue(confirmed)
        self.assertEqual(req_data["target"], agent_id)

        del_ok = agent_registry.delete_agent(req_data["target"])
        self.assertTrue(del_ok)
        self.assertIsNone(agent_registry.find_agent(agent_id))

    def test_self_heal_error_recovery(self):
        """Интеграционный тест: Self-Heal перехватывает сбой и применяет патч."""
        err_log = 'File "/app/main.py", line 15, in <module>\nAttributeError: module object has no attribute test'
        failing_file = extract_failing_file(err_log)
        self.assertEqual(failing_file, "main.py")

        with patch("engine.self_heal.get_repo_files") as mock_repo, \
             patch("engine.self_heal.query_model") as mock_model, \
             patch("engine.self_heal.push_project") as mock_push, \
             patch("engine.self_heal.restart_service") as mock_restart:

            mock_repo.return_value = {"main.py": "import os\nprint(os.test)"}
            mock_model.return_value = "import os\nprint('fixed')"
            mock_push.return_value = "https://github.com/MATIN0893/test-project"

            res = attempt_self_heal("test-project", "srv-render-id", err_log)
            self.assertTrue(res["success"])
            self.assertEqual(res["target_file"], "main.py")
            mock_push.assert_called_once()
            mock_restart.assert_called_once_with("srv-render-id")

    def test_health_report_integrity(self):
        """Интеграционный тест: health report формирует корректный JSON без маскировки."""
        with patch("services.health_service.check_github_status") as mock_gh:
            mock_gh.return_value = {"status": "http_404", "error": "Endpoint returned 404"}
            report = get_full_health_report()

            self.assertIn("status", report)
            # При ошибке 404 overall status должен отражать degraded, а не ok
            self.assertEqual(report["status"], "degraded")
            self.assertEqual(report["services"]["github"]["status"], "http_404")


if __name__ == "__main__":
    unittest.main()
