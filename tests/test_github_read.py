import unittest
import asyncio
import os
import sys
import json
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.github_service import (
    get_repo_metadata,
    get_repo_tree,
    get_repo_files_list,
    get_repo_file_content,
    get_repo_files,
    check_repo_exists,
)
from core.orchestrator import run_task
from core.task_engine import task_engine, TaskState


class TestGitHubReadPipeline(unittest.TestCase):

    def test_01_get_repo_metadata_success(self):
        """Проверка получения метаданных репозитория (200 OK)."""
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "name": "matin-agent",
                "full_name": "MATIN0893/matin-agent",
                "default_branch": "main",
                "private": True,
                "description": "Autonomous AI Agent",
                "html_url": "https://github.com/MATIN0893/matin-agent"
            }
            mock_get.return_value = mock_resp

            meta = get_repo_metadata("MATIN0893/matin-agent")
            self.assertTrue(meta["success"])
            self.assertEqual(meta["name"], "matin-agent")
            self.assertEqual(meta["default_branch"], "main")
            self.assertTrue(meta["private"])
            self.assertEqual(meta["status_code"], 200)
            self.assertIn("https://api.github.com/repos/MATIN0893/matin-agent", meta["endpoint"])

    def test_02_get_repo_metadata_404_diagnostics(self):
        """Проверка детальной диагностики при 404 (без маскировки и без утечки секретов)."""
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp

            meta = get_repo_metadata("non-existent-repo")
            self.assertFalse(meta["success"])
            self.assertEqual(meta["status_code"], 404)
            self.assertIn("404", meta["error"])
            self.assertIn("MATIN0893/non-existent-repo", meta["repository"])
            self.assertNotIn("token", meta["error"].lower())

    def test_03_get_repo_tree(self):
        """Проверка получения git tree для ветки main."""
        with patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("requests.get") as mock_get:
            mock_meta.return_value = {
                "success": True,
                "full_name": "MATIN0893/matin-agent",
                "default_branch": "main",
                "private": True
            }
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "tree": [
                    {"path": "main.py", "type": "blob", "size": 4071},
                    {"path": "render.yaml", "type": "blob", "size": 753},
                    {"path": "requirements.txt", "type": "blob", "size": 121},
                    {"path": "app", "type": "tree"},
                    {"path": "app/core.py", "type": "blob", "size": 500}
                ],
                "truncated": False
            }
            mock_get.return_value = mock_resp

            tree_res = get_repo_tree("matin-agent")
            self.assertTrue(tree_res["success"])
            self.assertEqual(tree_res["branch"], "main")
            self.assertIn("main.py", tree_res["files"])
            self.assertIn("render.yaml", tree_res["files"])
            self.assertIn("app/core.py", tree_res["files"])
            self.assertEqual(len(tree_res["files"]), 4)

    def test_04_get_repo_file_content_raw(self):
        """Проверка чтения конкретного файла по raw endpoint с auth-заголовками."""
        with patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("requests.get") as mock_get:
            mock_meta.return_value = {
                "success": True,
                "default_branch": "main",
                "full_name": "MATIN0893/matin-agent"
            }
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "import os\nprint('Hello from matin-agent main.py')"
            mock_get.return_value = mock_resp

            res = get_repo_file_content("matin-agent", "main.py")
            self.assertTrue(res["success"])
            self.assertEqual(res["path"], "main.py")
            self.assertEqual(res["branch"], "main")
            self.assertIn("Hello from matin-agent", res["content"])

    def test_05_read_only_orchestrator_pipeline_no_save_error(self):
        """
        КРИТИЧЕСКИЙ ТЕСТ:
        При READ-ONLY запросе 'Проверь репозиторий MATIN0893/matin-agent'
        агент НЕ должен падать с '⚠️ Не удалось получить файлы для сохранения.'
        """
        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_repo_tree") as mock_tree, \
             patch("services.github_service.get_repo_files") as mock_files:

            mock_ask.return_value = json.dumps({
                "action": "read",
                "project_name": "matin-agent",
                "target_file": "",
                "task_description": "Проверь репозиторий MATIN0893/matin-agent",
                "env": {}
            })

            mock_meta.return_value = {
                "success": True,
                "name": "matin-agent",
                "full_name": "MATIN0893/matin-agent",
                "default_branch": "main",
                "private": True,
                "description": "Matin Coder Telegram Bot",
                "html_url": "https://github.com/MATIN0893/matin-agent",
                "status_code": 200
            }

            mock_tree.return_value = {
                "success": True,
                "branch": "main",
                "files": ["main.py", "render.yaml", "requirements.txt", "Dockerfile"],
                "status_code": 200
            }

            mock_files.return_value = {
                "main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
                "render.yaml": "services:\n  - name: matin-agent\n"
            }

            result = asyncio.run(run_task("Проверь репозиторий MATIN0893/matin-agent"))

            self.assertNotIn("Не удалось получить файлы для сохранения", result)
            self.assertIn("ИНСПЕКЦИЯ РЕПОЗИТОРИЯ", result)
            self.assertIn("matin-agent", result)
            self.assertIn("READ-ONLY", result)
            self.assertIn("main.py", result)

    def test_06_read_specific_file_orchestrator(self):
        """Проверка чтения конкретного файла через orchestrator (READ-ONLY)."""
        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_repo_file_content") as mock_file:

            mock_ask.return_value = json.dumps({
                "action": "read",
                "project_name": "matin-agent",
                "target_file": "main.py",
                "task_description": "Прочитай main.py в репозитории matin-agent",
                "env": {}
            })

            mock_meta.return_value = {
                "success": True,
                "default_branch": "main",
                "full_name": "MATIN0893/matin-agent",
                "html_url": "https://github.com/MATIN0893/matin-agent"
            }

            mock_file.return_value = {
                "success": True,
                "content": "# FastAPI App for Matin Agent\nimport os\n",
                "path": "main.py",
                "status_code": 200,
                "branch": "main"
            }

            result = asyncio.run(run_task("Прочитай main.py в репозитории matin-agent"))

            self.assertNotIn("Не удалось получить файлы для сохранения", result)
            self.assertIn("main.py", result)
            self.assertIn("FastAPI App for Matin Agent", result)

    def test_07_read_only_failed_access_diagnostics(self):
        """Проверка диагностики при сбое доступа к репозиторию."""
        with patch("core.orchestrator.ask") as mock_ask, \
             patch("services.github_service.get_repo_metadata") as mock_meta, \
             patch("services.github_service.get_user_repositories") as mock_user_repos:

            mock_ask.return_value = json.dumps({
                "action": "read",
                "project_name": "unknown-repo-xyz",
                "target_file": "",
                "task_description": "Проверь unknown-repo-xyz"
            })

            mock_meta.return_value = {
                "success": False,
                "status_code": 404,
                "error": "Репозиторий 'MATIN0893/unknown-repo-xyz' не найден (HTTP 404 Not Found).",
                "endpoint": "https://api.github.com/repos/MATIN0893/unknown-repo-xyz",
                "repository": "MATIN0893/unknown-repo-xyz"
            }
            mock_user_repos.return_value = []

            result = asyncio.run(run_task("Проверь unknown-repo-xyz"))

            self.assertIn("Ошибка доступа к репозиторию", result)
            self.assertIn("404", result)
            self.assertIn("https://api.github.com/repos/MATIN0893/unknown-repo-xyz", result)
            self.assertNotIn("Не удалось получить файлы для сохранения", result)


if __name__ == "__main__":
    unittest.main()
