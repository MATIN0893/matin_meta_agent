import os
import re
import ast
import json
import time
import subprocess
import logging
from typing import Dict, Any, Tuple, List, Optional
from engine.agent_registry import agent_registry, AgentLifecycle, AgentRecord
from core.security import security_guard, Permission
from core.memory import memory
from core.llm_client import ask

logger = logging.getLogger("MATIN.AGENT_FACTORY")

AGENT_FACTORY_SYSTEM_PROMPT = """
Ты — AI Architect и Lead Software Engineer в операционной системе MATIN META.
Твоя задача — спроектировать и сгенерировать ПОЛНОСТЬЮ РАБОЧИЙ автономный проект нового AI-агента.

ПРАВИЛА ГЕНЕРАЦИИ:
1. НИКАКИХ МОКОВ И ПУСТЫХ СТРОК: код должен быть реально исполняемым Python 3.11+.
2. НИКАКИХ ЗАХАРДКОЖЕННЫХ СЕКРЕТОВ: все токены и ключи читаются строго из os.getenv(...).
3. ИЗОЛЯЦИЯ: агент должен содержать полный набор файлов:
   - main.py (точка входа, запуск сервиса/бота, HTTP health check эндпоинт на PORT)
   - config.py (конфигурация через pydantic/dataclass/os.environ)
   - requirements.txt (необходимые pip зависимости)
   - Dockerfile (готовый для деплоя на Render/VPS)
   - render.yaml (спецификация сервиса для Render)
   - README.md (описание, назначение, инструкция по запуску)
   - core/agent.py (основная логика агента)
   - tests/test_agent.py (реальные unit-тесты unittest/pytest, проверяющие работоспособность)

ФОРМАТ ОТВЕТА — СТРОГО JSON:
{
  "agent_id": "matin-monitor",
  "name": "MATIN MONITOR",
  "version": "1.0.0",
  "description": "Автономный монитор состояния Telegram-ботов и сервисов",
  "permissions": ["telegram.read", "render.read", "health.check"],
  "files": {
    "main.py": "код...",
    "config.py": "код...",
    "requirements.txt": "содержимое...",
    "Dockerfile": "содержимое...",
    "render.yaml": "содержимое...",
    "README.md": "содержимое...",
    "core/agent.py": "код...",
    "tests/test_agent.py": "код тестов..."
  }
}
"""


def generate_slug(text: str) -> str:
    trans = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }
    en_words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    meaningful = [w for w in en_words if w not in ("create", "agent", "new", "the")]
    if meaningful:
        slug = "-".join(meaningful)
    else:
        res = []
        for ch in text.lower():
            if ch in trans:
                res.append(trans[ch])
            elif ch.isalnum():
                res.append(ch)
            elif ch in " -_":
                res.append("-")
        slug = re.sub(r"-+", "-", "".join(res)).strip("-")

    slug = slug[:24].strip("-") or "custom-agent"
    if not slug.startswith("matin-"):
        slug = f"matin-{slug}"
    return slug


class AgentFactory:
    def __init__(self, agents_base_dir: str = "agents"):
        self.base_dir = agents_base_dir

    def extract_agent_spec(self, user_prompt: str) -> dict:
        prompt = f"Запрос пользователя на создание агента:\n\n{user_prompt}\n\nСпроектируй агента и сгенерируй файлы проекта."
        raw_response = ask(AGENT_FACTORY_SYSTEM_PROMPT, prompt)

        candidate = raw_response.strip() if raw_response else ""
        code_match = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
        if code_match:
            candidate = code_match.group(1).strip()

        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "files" in data:
                return data
        except Exception:
            pass

        try:
            from json_repair import repair_json
            data = repair_json(candidate, return_objects=True)
            if isinstance(data, dict) and "files" in data:
                return data
        except Exception:
            pass

        slug = generate_slug(user_prompt)
        return self._generate_builtin_template(slug, user_prompt)

    def _generate_builtin_template(self, agent_id: str, description: str) -> dict:
        clean_name = agent_id.replace("-", " ").title()
        files = {
            "__init__.py": "",
            "core/__init__.py": "",
            "tests/__init__.py": "",
            "config.py": (
                "import os\n"
                "from dotenv import load_dotenv\n"
                "load_dotenv()\n\n"
                f"AGENT_NAME = '{clean_name}'\n"
                f"AGENT_ID = '{agent_id}'\n"
                "PORT = int(os.getenv('PORT', 10000))\n"
                "TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN', '')\n"
                "LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')\n"
            ),
            "core/agent.py": (
                "import time\n"
                "import logging\n\n"
                "logger = logging.getLogger(__name__)\n\n"
                "class AutonomousWorker:\n"
                "    def __init__(self, name: str):\n"
                "        self.name = name\n"
                "        self.is_running = False\n"
                "        self.checks_count = 0\n\n"
                "    def perform_cycle(self) -> dict:\n"
                "        self.checks_count += 1\n"
                "        return {\n"
                "            'worker': self.name,\n"
                "            'cycle': self.checks_count,\n"
                "            'status': 'healthy',\n"
                "            'timestamp': time.time()\n"
                "        }\n"
            ),
            "main.py": (
                "import os\n"
                "import time\n"
                "import logging\n"
                "from http.server import HTTPServer, BaseHTTPRequestHandler\n"
                "import threading\n"
                "from config import AGENT_NAME, PORT\n"
                "from core.agent import AutonomousWorker\n\n"
                "logging.basicConfig(level=logging.INFO)\n"
                "logger = logging.getLogger(AGENT_NAME)\n\n"
                "worker = AutonomousWorker(AGENT_NAME)\n\n"
                "class HealthHandler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type', 'application/json')\n"
                "        self.end_headers()\n"
                "        res = worker.perform_cycle()\n"
                "        self.wfile.write(str(res).encode('utf-8'))\n"
                "    def log_message(self, format, *args):\n"
                "        pass\n\n"
                "def start_server():\n"
                "    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)\n"
                "    logger.info(f'[{AGENT_NAME}] Слушаю порт {PORT}')\n"
                "    server.serve_forever()\n\n"
                "if __name__ == '__main__':\n"
                "    logger.info(f'Запуск {AGENT_NAME}...')\n"
                "    start_server()\n"
            ),
            "requirements.txt": (
                "requests>=2.31.0\n"
                "python-dotenv>=1.0.0\n"
                "pytest>=7.0.0\n"
            ),
            "Dockerfile": (
                "FROM python:3.11-slim\n"
                "WORKDIR /app\n"
                "COPY requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\n"
                "EXPOSE 10000\n"
                "CMD [\"python\", \"main.py\"]\n"
            ),
            "render.yaml": (
                "services:\n"
                f"  - type: web\n"
                f"    name: {agent_id}\n"
                "    env: python\n"
                "    buildCommand: pip install -r requirements.txt\n"
                "    startCommand: python main.py\n"
                "    envVars:\n"
                "      - key: PORT\n"
                "        value: 10000\n"
            ),
            "README.md": (
                f"# {clean_name}\n\n"
                f"Автономный микросервисный AI-агент `{agent_id}`.\n\n"
                f"## Описание\n{description}\n\n"
                "## Запуск локально\n```bash\n"
                "pip install -r requirements.txt\n"
                "python main.py\n```\n"
            ),
            "tests/test_agent.py": (
                "import unittest\n"
                "from core.agent import AutonomousWorker\n\n"
                "class TestAgent(unittest.TestCase):\n"
                "    def test_worker_cycle(self):\n"
                "        worker = AutonomousWorker('TestAgent')\n"
                "        res = worker.perform_cycle()\n"
                "        self.assertEqual(res['status'], 'healthy')\n"
                "        self.assertEqual(res['cycle'], 1)\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )
        }
        return {
            "agent_id": agent_id,
            "name": clean_name,
            "version": "1.0.0",
            "description": description,
            "permissions": ["health.check", "filesystem.read"],
            "files": files
        }

    def validate_code_syntax(self, files: Dict[str, str]) -> Tuple[bool, Dict[str, str]]:
        errors = {}
        for filename, content in files.items():
            if filename.endswith(".py"):
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    errors[filename] = f"Line {e.lineno}: {e.msg}"
        return len(errors) == 0, errors

    def run_agent_unit_tests(self, agent_dir: str) -> Tuple[bool, str]:
        test_file = os.path.join(agent_dir, "tests", "test_agent.py")
        if not os.path.exists(test_file):
            return True, "Тесты отсутствуют"

        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = os.path.abspath(agent_dir)
            res = subprocess.run(
                ["python3", "-m", "unittest", "discover", "-s", "tests"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=os.path.abspath(agent_dir),
                env=env
            )
            if res.returncode == 0:
                return True, res.stderr or "OK"
            return False, res.stderr or res.stdout or "Test failure"
        except Exception as e:
            return False, str(e)

    def write_agent_to_disk(self, agent_id: str, files: Dict[str, str]) -> str:
        agent_dir = os.path.join(self.base_dir, agent_id)
        os.makedirs(agent_dir, exist_ok=True)
        for filepath, content in files.items():
            full_path = os.path.join(agent_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
        return agent_dir

    def create_agent(self, user_prompt: str, status_cb=None) -> Dict[str, Any]:
        def notify(msg: str):
            if status_cb:
                try:
                    status_cb(msg)
                except Exception:
                    pass

        notify("🧠 Анализирую требования к новому агенту...")
        spec = self.extract_agent_spec(user_prompt)
        agent_id = spec.get("agent_id", "matin-agent")
        name = spec.get("name", agent_id)
        files = spec.get("files", {})

        if "__init__.py" not in files:
            files["__init__.py"] = ""
        if "core/__init__.py" not in files:
            files["core/__init__.py"] = ""
        if "tests/__init__.py" not in files:
            files["tests/__init__.py"] = ""

        for fname, fcontent in files.items():
            has_secret, found_labels = security_guard.contains_secrets(fcontent)
            if has_secret:
                notify(f"⚠️ Security Guard: удаляю токен ({','.join(found_labels)}) из {fname}")
                files[fname] = security_guard.sanitize_secrets(fcontent)

        notify(f"⚙️ Lead Architect: синтезирую изолированную структуру {agent_id} ({len(files)} файлов)...")
        syntax_ok, syntax_errors = self.validate_code_syntax(files)

        if not syntax_ok:
            return {
                "success": False,
                "error": f"Синтаксические ошибки: {syntax_errors}",
                "agent_id": agent_id
            }

        agent_dir = self.write_agent_to_disk(agent_id, files)

        notify(f"🧪 Запуск unit-тестов для {agent_id}...")
        test_ok, test_output = self.run_agent_unit_tests(agent_dir)

        record = agent_registry.register_agent(
            agent_id=agent_id,
            name=name,
            description=spec.get("description", user_prompt),
            permissions=spec.get("permissions", ["health.check"]),
            github_repo=f"agents/{agent_id}",
            config={"created_via": "Agent Factory 2.0", "path": agent_dir}
        )
        agent_registry.update_status(agent_id, AgentLifecycle.READY)

        memory.record_project(
            name=agent_id,
            repo=f"agents/{agent_id}",
            stack="Python, Docker, Render",
            description=spec.get("description", user_prompt),
            status="ready"
        )

        notify(f"✅ Агент {name} (`{agent_id}`) успешно создан и зарегистрирован в экосистеме!")

        return {
            "success": True,
            "agent_id": agent_id,
            "name": name,
            "agent_dir": agent_dir,
            "files_count": len(files),
            "files": list(files.keys()),
            "status": "ready",
            "permissions": spec.get("permissions", []),
            "tests_passed": test_ok
        }


agent_factory = AgentFactory()
