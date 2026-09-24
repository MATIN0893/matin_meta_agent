import ast
import asyncio
import inspect
import json
import re
from core.llm_client import ask
from core.prompts import (
    PLANNER_SYSTEM,
    PLANNER_USER,
    MODIFIER_SYSTEM,
    MODIFIER_USER,
    ENGINEER_SYSTEM,
    ENGINEER_USER,
    REVIEWER_SYSTEM,
    REVIEWER_USER,
    FIXER_SYSTEM,
    FIXER_USER,
)
from services.github_service import (
    list_user_repos,
    get_repo_files,
    push_project,
    delete_repo,
)

try:
    from services.github_service import delete_repo_file
except ImportError:
    def delete_repo_file(repo_name: str, file_path: str):
        return False


def safe_parse_json(text: str) -> dict:
    """Извлекает и парсит JSON даже при наличии markdown-разметки."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


def get_field(data, key: str, default=None):
    if isinstance(data, dict):
        return data.get(key, default)
    return default


async def run_task(prompt: str, status_cb=None) -> str:
    """Асинхронная точка входа для оркестратора задач."""
    async def notify(msg: str):
        if status_cb:
            try:
                if inspect.iscoroutinefunction(status_cb):
                    await status_cb(msg)
                else:
                    status_cb(msg)
            except Exception:
                pass

    await notify("🧠 Анализирую задачу и составляю план...")
    user_repos = await asyncio.to_thread(list_user_repos)
    repo_names_str = ", ".join(user_repos) if user_repos else "нет репозиториев"

    try:
        planner_prompt = PLANNER_SYSTEM.format(repo_names=repo_names_str)
    except Exception:
        planner_prompt = PLANNER_SYSTEM

    plan_raw = await asyncio.to_thread(ask, planner_prompt, PLANNER_USER.format(user_prompt=prompt))
    try:
        plan = safe_parse_json(plan_raw)
    except Exception as e:
        return f"❌ Ошибка разбора плана: {e}"

    action = get_field(plan, "action", default="modify")
    project_name = get_field(plan, "project_name", default="matin-agent")
    target_file = get_field(plan, "target_file", default="")
    task_desc = get_field(plan, "task_description", default=prompt)
    extracted_env = get_field(plan, "env", default={})

    # Сценарий: Удаление
    if action == "delete":
        if target_file:
            await notify(f"🗑 Удаляю файл {target_file} в репозитории {project_name}...")
            ok = await asyncio.to_thread(delete_repo_file, project_name, target_file)
            return f"✅ Файл {target_file} удален." if ok else f"❌ Не удалось удалить файл {target_file}."
        else:
            await notify(f"🗑 Удаляю репозиторий {project_name}...")
            ok = await asyncio.to_thread(delete_repo, project_name)
            return f"✅ Репозиторий {project_name} успешно удален." if ok else f"❌ Не удалось удалить репозиторий {project_name}."

    files = {}

    # Сценарий: Модификация (MODIFIER)
    if action == "modify":
        await notify(f"📥 Скачиваю файлы проекта {project_name}...")
        existing_files = await asyncio.to_thread(get_repo_files, project_name)
        if not existing_files:
            return f"⚠️ Репозиторий {project_name} пуст или не найден на GitHub."

        await notify(f"⚙️ Senior Maintainer: пересобираю кодовую базу {project_name}...")

        # Фильтруем мусор и укладываемся в лимит Groq
        SKIP_EXT = {'.md', '.txt', '.log', '.lock', '.png', '.jpg', '.svg', '.ico'}
        SKIP_DIRS = {'node_modules', '.git', '__pycache__', 'dist', 'build', '.venv'}

        def should_skip(path: str) -> bool:
            parts = path.replace('\\', '/').split('/')
            if any(d in SKIP_DIRS for d in parts):
                return True
            ext = '.' + path.rsplit('.', 1)[-1].lower() if '.' in path else ''
            return ext in SKIP_EXT

        MAX_FILE_CHARS = 2500
        MAX_TOTAL_CHARS = 18000
        filtered = {p: c for p, c in existing_files.items() if not should_skip(p)}
        chunks = []
        total = 0
        for path, content in sorted(filtered.items(), key=lambda x: len(x[1])):
            snippet = content[:MAX_FILE_CHARS]
            chunk = f"=== {path} ===\n{snippet}"
            if total + len(chunk) > MAX_TOTAL_CHARS:
                break
            chunks.append(chunk)
            total += len(chunk)
        files_dump = "\n\n".join(chunks)

        mod_raw = await asyncio.to_thread(
            ask,
            MODIFIER_SYSTEM,
            MODIFIER_USER.format(
                task_description=task_desc,
                extracted_env=json.dumps(extracted_env, ensure_ascii=False),
                files=files_dump,
                user_prompt=prompt,
            ),
        )
        try:
            mod_data = safe_parse_json(mod_raw)
        except Exception as e:
            return f"❌ Ошибка разбора модификации: {e}"
        files = mod_data.get("files", {})

    # Сценарий: Создание нового проекта (ENGINEER)
    else:
        await notify(f"⚙️ Lead Engineer: генерирую проект {project_name}...")
        eng_raw = await asyncio.to_thread(
            ask,
            ENGINEER_SYSTEM,
            ENGINEER_USER.format(
                task_description=task_desc,
                extracted_env=json.dumps(extracted_env, ensure_ascii=False),
                user_prompt=prompt,
            ),
        )
        try:
            eng_data = safe_parse_json(eng_raw)
        except Exception as e:
            return f"❌ Ошибка разбора генерации: {e}"
        files = eng_data.get("files", {})

    if not files:
        return "⚠️ Не удалось получить файлы для сохранения."

    # Рецензент (REVIEWER)
    await notify("🔍 Code Reviewer: проверяю качество кода...")
    review_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in files.items()])
    try:
        review_raw = await asyncio.to_thread(
            ask,
            REVIEWER_SYSTEM,
            REVIEWER_USER.format(files=review_dump, user_prompt=prompt),
        )
        review_data = safe_parse_json(review_raw)
        status = review_data.get("status", "APPROVED")
    except Exception:
        status = "APPROVED"
        review_data = {}

    # Доработка замечаний (FIXER)
    if status != "APPROVED":
        await notify("🔧 Bug Fixer: устраняю замечания...")
        try:
            fix_raw = await asyncio.to_thread(
                ask,
                FIXER_SYSTEM,
                FIXER_USER.format(
                    files=review_dump,
                    issues=json.dumps(review_data.get("issues", []), ensure_ascii=False),
                    user_prompt=prompt,
                ),
            )
            fix_data = safe_parse_json(fix_raw)
            files = fix_data.get("files", files)
        except Exception:
            pass

    # Пуш в GitHub
    await notify(f"🚀 Загружаю код в GitHub репозиторий {project_name}...")
    repo_url = await asyncio.to_thread(push_project, project_name, files)
    return f"✅ Проект {project_name} успешно обновлен и опубликован на GitHub!\n🔗 {repo_url}"

orchestrate = run_task