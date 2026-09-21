from core.llm_client import ask
from core.prompts import ARCHITECT_SYSTEM, FIXER_SYSTEM
from services.sandbox_service import validate_project_files
from services.github_service import push_project
from services.deploy_service import trigger_deploy
import json, re

MAX_FIX_ATTEMPTS = 3

def extract_json(text: str) -> dict:
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)

async def run_task(description: str, progress_cb=None) -> str:
    try:
        if progress_cb:
            await progress_cb("🧠 Проектирую структуру...")

        raw = ask(ARCHITECT_SYSTEM, description)
        project = extract_json(raw)
        files = project["files"]
        project_name = project["project_name"]

        for attempt in range(MAX_FIX_ATTEMPTS):
            errors = validate_project_files(files)
            if not errors:
                break
            if progress_cb:
                await progress_cb(f"🔧 Исправляю ошибки (попытка {attempt+1})...")
            for filename, err in errors.items():
                fixed = ask(FIXER_SYSTEM, f"Файл: {filename}\nОшибка: {err}\n\nКод:\n{files[filename]}")
                files[filename] = fixed

        if progress_cb:
            await progress_cb("📦 Пушу на GitHub...")
        try:
            repo_url = push_project(project_name, files)
            github_status = f"GitHub: {repo_url}"
        except Exception as e:
            github_status = f"GitHub ошибка: {e}"

        if progress_cb:
            await progress_cb("🚀 Деплою на Render...")
        deploy_status = trigger_deploy()

        return (
            f"✅ Готово!\n"
            f"Проект: `{project_name}`\n"
            f"Файлов: {len(files)}\n"
            f"{github_status}\n"
            f"Render: {deploy_status}"
        )

    except json.JSONDecodeError as e:
        return f"❌ JSON ошибка: {e}\n\nОтвет LLM:\n{raw[:500]}"
    except Exception as e:
        return f"❌ Ошибка: {e}"
