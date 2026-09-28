import unittest
import ast
import re
import os
import sys
from unittest.mock import patch, MagicMock

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.orchestrator import collect_project_files
from services.github_service import check_repo_exists, find_matching_repo, normalize_repo_name
from core.llm_client import call_llm, DEFAULT_GROQ_MODELS, DEFAULT_OPENROUTER_MODELS, DEPRECATED_MODELS
from engine.self_heal import extract_failing_file, validate_python_code, attempt_self_heal
from engine.agent_registry import agent_registry, AgentRecord, AgentLifecycle
from engine.agent_factory import agent_factory
from core.security import security_guard


class TestFixes(unittest.TestCase):

    def test_01_regex_unbalanced_parenthesis_fix(self):
        """Тест 1: Проверка отсутствия 'unbalanced parenthesis at position 109' в regex парсере."""
        sample_output = (
            "Вот обновленный проект:\n\n"
            "=== main.py ===\n"
            "import os\n"
            "print('Hello world!')\n\n"
            "=== utils/helpers.py ===\n"
            "def add(a, b):\n"
            "    return a + b\n\n"
            "=== config.py ===\n"
            "PORT = 8080\n"
        )

        # Вызов collect_project_files без JSON (fallback на regex парсер)
        extracted = collect_project_files(parsed_data={}, raw_text=sample_output)

        self.assertIn("main.py", extracted)
        self.assertIn("utils/helpers.py", extracted)
        self.assertIn("config.py", extracted)
        self.assertIn("print('Hello world!')", extracted["main.py"])
        self.assertIn("PORT = 8080", extracted["config.py"])

        # Одиночный блок
        single_output = "=== worker.py ===\nclass Worker:\n    pass\n"
        single_extracted = collect_project_files(parsed_data={}, raw_text=single_output)
        self.assertIn("worker.py", single_extracted)

    def test_02_github_mcp_404_not_masked(self):
        """Тест 2: Проверка реального endpoint и того, что 404 не маскируется как OK."""
        # 1. Проверяем check_repo_exists для несуществующего репозитория gemini-github-mcp
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp

            status_info = check_repo_exists("gemini-github-mcp")
            self.assertFalse(status_info["exists"])
            self.assertEqual(status_info["status_code"], 404)
            self.assertIn("404", status_info["message"])
            # Убеждаемся, что статус НЕ маскируется под 200 или True
            self.assertNotEqual(status_info["message"], "OK")

        # 2. Проверяем обращение к правильному endpoint
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            status_info = check_repo_exists("MATIN0893/matin_meta_agent")
            self.assertTrue(status_info["exists"])
            self.assertEqual(status_info["status_code"], 200)
            mock_get.assert_called_with(
                "https://api.github.com/repos/MATIN0893/matin_meta_agent",
                headers=unittest.mock.ANY,
                timeout=10
            )

    def test_03_llm_router_cascade_fallback(self):
        """Тест 3: Проверка каскадного переключения LLM Router при недоступности модели."""
        # Убеждаемся, что устаревшие модели исключены
        for m in DEPRECATED_MODELS:
            self.assertNotIn(m, DEFAULT_GROQ_MODELS)
            self.assertNotIn(m, DEFAULT_OPENROUTER_MODELS)

        messages = [{"role": "user", "content": "ping"}]

        # Имитация: первая модель Groq вернула 404/400, вторая успешно вернула ответ
        with patch.dict(os.environ, {"GROQ_API_KEY": "dummy_groq", "OPENROUTER_API_KEY": "dummy_or"}):
            with patch("requests.post") as mock_post:
                # Первый вызов (модель 1) возвращает 404
                resp_fail = MagicMock()
                resp_fail.status_code = 404
                resp_fail.text = "Model not found"

                # Второй вызов (модель 2) возвращает 200 OK
                resp_ok = MagicMock()
                resp_ok.status_code = 200
                resp_ok.json.return_value = {
                    "choices": [{"message": {"content": "Fallback response SUCCESS"}}]
                }

                mock_post.side_effect = [resp_fail, resp_ok]

                result = call_llm(messages)
                self.assertEqual(result, "Fallback response SUCCESS")
                self.assertEqual(mock_post.call_count, 2)

    def test_04_self_heal_pipeline(self):
        """Тест 4: Проверка восстановления через Self-Heal (обнаружение + патч + коммит)."""
        sample_traceback = """
Traceback (most recent call last):
  File "/app/main.py", line 42, in <module>
    raise ValueError("Unexpected configuration")
ValueError: Unexpected configuration
        """
        failing_file = extract_failing_file(sample_traceback)
        self.assertEqual(failing_file, "main.py")

        # Проверка AST валидации
        valid_code = "def test():\n    return 42\n"
        invalid_code = "def test() return 42"
        self.assertTrue(validate_python_code(valid_code))
        self.assertFalse(validate_python_code(invalid_code))

        # Имитируем полный процесс attempt_self_heal
        with patch("engine.self_heal.get_repo_files") as mock_get_files, \
             patch("engine.self_heal.query_model") as mock_model, \
             patch("engine.self_heal.push_project") as mock_push, \
             patch("engine.self_heal.restart_service") as mock_restart:

            mock_get_files.return_value = {"main.py": "print('broken')\n"}
            mock_model.return_value = "print('fixed')\n"
            mock_push.return_value = "https://github.com/MATIN0893/test_repo"

            heal_res = attempt_self_heal(
                srv_name="test_repo",
                render_id="srv_12345",
                error_logs=sample_traceback
            )

            self.assertTrue(heal_res["success"])
            self.assertEqual(heal_res["target_file"], "main.py")
            mock_push.assert_called_once()
            mock_restart.assert_called_with("srv_12345")

    def test_05_agent_factory_preserved(self):
        """Тест 5: Проверка, что Agent Factory работает и ничего не сломано."""
        # Генерация спецификации агента
        spec = agent_factory._generate_builtin_template("matin-test-bot", "Тестовый бот для проверки")
        self.assertEqual(spec["agent_id"], "matin-test-bot")
        self.assertIn("main.py", spec["files"])
        self.assertIn("tests/test_agent.py", spec["files"])

        # Валидация синтаксиса
        ok, errs = agent_factory.validate_code_syntax(spec["files"])
        self.assertTrue(ok, f"Синтаксические ошибки: {errs}")

    def test_06_delete_agent_security_guard_confirmation(self):
        """Тест 6: Проверка удаления агента через Security Guard с подтверждением."""
        # Регистрируем временного агента
        agent_registry.register_agent(
            agent_id="matin-to-delete",
            name="Delete Me Agent",
            description="Agent for deletion test"
        )
        self.assertIsNotNone(agent_registry.find_agent("matin-to-delete"))
        self.assertIsNotNone(agent_registry.find_agent("Delete Me Agent"))

        # Создаем запрос на подтверждение
        user_id = 999999
        warn_msg = security_guard.create_confirmation_request(user_id, "delete_agent", "matin-to-delete")
        self.assertIn("ПОДТВЕРЖДАЮ", warn_msg)

        # Проверка неверного подтверждения
        confirmed, _ = security_guard.check_confirmation(user_id, "ОТМЕНА")
        self.assertFalse(confirmed)

        # Извлекаем код подтверждения
        pending = security_guard.pending_confirmations.get(user_id)
        self.assertIsNotNone(pending)
        code = pending["code"]

        # Проверка верного подтверждения
        confirmed, req = security_guard.check_confirmation(user_id, f"ПОДТВЕРЖДАЮ {code}")
        self.assertTrue(confirmed)
        self.assertEqual(req["target"], "matin-to-delete")

        # Удаление агента из реестра
        deleted = agent_registry.delete_agent(req["target"])
        self.assertTrue(deleted)
        self.assertIsNone(agent_registry.find_agent("matin-to-delete"))


if __name__ == "__main__":
    unittest.main()
