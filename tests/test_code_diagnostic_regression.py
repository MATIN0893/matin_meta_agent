import unittest
import asyncio
import os
import sys
import json
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.orchestrator import run_task, classify_task_intent
from core.task_engine import task_engine, TaskState


class TestCodeDiagnosticRegression(unittest.TestCase):

    def test_01_classification_regression(self):
        """
        Регрессионный тест:
        INPUT: Найди точный источник ошибки 429 Too Many Requests в MATIN0893/matin-agent. Ничего не изменяй.
        EXPECTED: task_type = CODE_DIAGNOSTIC
        Проверка, что диагностическая задача НЕ превращается в обычную repository inspection.
        """
        user_input = "Найди точный источник ошибки 429 Too Many Requests в MATIN0893/matin-agent. Ничего не изменяй."
        task_type = classify_task_intent(user_input)

        self.assertEqual(task_type, "CODE_DIAGNOSTIC")
        self.assertNotEqual(task_type, "REPOSITORY_INSPECTION")
        self.assertNotEqual(task_type, "CODE_MODIFICATION")

    def test_02_preservation_of_intent_after_repository_context(self):
        """
        Тест подтверждает, что после этапа REPOSITORY_CONTEXT сохраняется исходный task intent,
        задача не завершается досрочно на инспекции и проходит полный пайплайн:
        UNDERSTAND -> REPOSITORY_CONTEXT -> READ_FILES -> SEARCH -> TRACE -> ANALYZE -> REPORT.
        FORBIDDEN: MODIFY, COMMIT, PUSH.
        """
        user_input = "Найди точный источник ошибки 429 Too Many Requests в MATIN0893/matin-agent. Ничего не изменяй."

        mock_files = {
            "main.py": """
import os
from groq import Groq
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

async def code_command(update, context):
    prompt = " ".join(context.args)
    completion = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile"
    )
    await update.message.reply_text(completion.choices[0].message.content)
""",
            "app/ai_agent.py": """
import httpx
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

async def _request_model(client, model, headers, base_payload):
    for attempt in range(3):
        response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        if response.status_code != 429:
            return response
        await asyncio.sleep(2.0)
    return response
"""
        }

        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_repo_tree") as mock_tree, \
             patch("services.github_service.get_repo_files") as mock_repo_files, \
             patch("services.github_service.push_project") as mock_push:

            mock_meta.return_value = {
                "success": True,
                "name": "matin-agent",
                "full_name": "MATIN0893/matin-agent",
                "default_branch": "main",
                "private": True,
                "html_url": "https://github.com/MATIN0893/matin-agent"
            }

            mock_tree.return_value = {
                "success": True,
                "branch": "main",
                "files": ["main.py", "app/ai_agent.py", "render.yaml"],
                "status_code": 200
            }

            mock_repo_files.return_value = mock_files

            def mock_ask_handler(system_prompt, user_prompt):
                if "Planner" in system_prompt:
                    return json.dumps({
                        "task_type": "CODE_DIAGNOSTIC",
                        "action": "diagnostic",
                        "project_name": "MATIN0893/matin-agent",
                        "target_file": "",
                        "search_queries": ["429", "openrouter", "Groq", "fallback", "retry"],
                        "task_description": user_input
                    })
                else:
                    return """
1. 🎯 Цель диагностики: поиск источника 429 Too Many Requests в MATIN0893/matin-agent.
2. 📍 Найденные источники:
   - main.py (строка 10): прямой вызов groq_client.chat.completions.create(model='llama-3.3-70b-versatile') без retry и fallback.
   - app/ai_agent.py (строки 6-12): OPENROUTER_URL с обработкой 429 через _request_model.
3. 🔄 Execution Path: Telegram code_command -> Groq API (лимит квоты 429) -> unhandled exception.
4. ⚠️ Первопричина ошибки 429: исчерпание квоты Groq/OpenRouter.
5. 💡 Рекомендации: добавить экспоненциальный retry и каскадный переключатель.
6. 🔒 Статус: READ-ONLY (изменения не вносились).
"""

            mock_ask.side_effect = mock_ask_handler

            result = asyncio.run(run_task(user_input))

            self.assertIn("ИНЖЕНЕРНАЯ ДИАГНОСТИКА", result)
            self.assertIn("429", result)
            self.assertIn("Execution Path", result)
            self.assertIn("main.py", result)
            self.assertIn("READ-ONLY", result)
            self.assertNotIn("READ-ONLY инспекция завершена", result)
            self.assertNotIn("Не удалось получить файлы для сохранения", result)

            mock_push.assert_not_called()

            active_or_last = list(task_engine.tasks.values())[-1]
            self.assertEqual(active_or_last.plan["task_type"], "CODE_DIAGNOSTIC")
            self.assertEqual(active_or_last.plan["original_intent"], user_input)

            logged_actions = [entry["action"] for entry in active_or_last.audit_trail]
            print("Logged actions in audit_trail:", logged_actions)

            self.assertIn("understand", logged_actions)
            self.assertIn("repository_context", logged_actions)
            self.assertIn("read_files", logged_actions)
            self.assertIn("search", logged_actions)
            self.assertIn("trace", logged_actions)
            self.assertIn("analyze", logged_actions)
            self.assertIn("report", logged_actions)

            u_idx = logged_actions.index("understand")
            rc_idx = logged_actions.index("repository_context")
            rf_idx = logged_actions.index("read_files")
            s_idx = logged_actions.index("search")
            t_idx = logged_actions.index("trace")
            a_idx = logged_actions.index("analyze")
            r_idx = logged_actions.index("report")

            self.assertTrue(u_idx < rc_idx < rf_idx < s_idx < t_idx < a_idx < r_idx)

    def test_03_inspection_vs_diagnostic_contrast(self):
        """Проверка контраста: инспекция выдает структуру, а диагностика запускает глубокий анализ."""
        inspection_query = "Покажи структуру репозитория MATIN0893/matin-agent"
        diagnostic_query = "Найди все упоминания openrouter, api/v1/chat/completions, 429, Groq, fallback, retry и проследи Telegram → LLM → response"

        self.assertEqual(classify_task_intent(inspection_query), "REPOSITORY_INSPECTION")
        self.assertEqual(classify_task_intent(diagnostic_query), "CODE_DIAGNOSTIC")


if __name__ == "__main__":
    unittest.main()
