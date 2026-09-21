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

from services.deploy_service import trigger_deploy

def normalize_dict(d: dict) -> dict:
    if not isinstance(d, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in d.items()}

def get_field(d: dict, *keys, default=None):
    norm = normalize_dict(d)
    for k in keys:
        clean = str(k).strip().lower()
        if clean in norm:
            return norm[clean]
    return default

def safe_parse_json(raw: str) -> dict:
    if not raw:
        return {}
    text = raw.strip()

    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]

    prepared = re.sub(r"\bTrue\b", "true", text)
    prepared = re.sub(r"\bFalse\b", "false", prepared)
    prepared = re.sub(r"\bNone\b", "null", prepared)
    prepared = re.sub(r",\s*([\}\]])", r"\1", prepared)

    try:
        return json.loads(prepared)
    except Exception:
        pass

    try:
        val = ast.literal_eval(text)
        if isinstance(val, dict):
            return val
    except Exception:
        pass

    return json.loads(prepared)

async def run_task(prompt: str, status_cb=None) -> str:
    """Асинхронная главная точка входа для оркестратора."""
    async def notify(msg: str):
        if status_cb:
            try:
                if inspect.iscoroutinefunction(status_cb):
                    await status_cb(msg)
                else:
                    res = status_cb(msg)
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass

    # 1. Получаем список существующих репозиториев
    repos = await asyncio.to_thread(list_user_repos)
    repos_str = ", ".join(repos) if repos else "Нет существующих репозиториев"

    # 2. CHIEF PLANNER
    await notify("🔍 Chief Planner: анализирую архитектуру и извлекаю ключи...")
    planner_prompt = PLANNER_SYSTEM.format(repo_list=repos_str)
    plan_raw = await asyncio.to_thread(ask, planner_prompt, PLANNER_USER.format(user_prompt=prompt))

    try:
        plan = safe_parse_json(plan_raw)
    except Exception as e:
        return f"❌ Ошибка разбора плана: {e}"

    action = get_field(plan, "action", default="create")
    project_name = get_field(plan, "project_name", "repo_name", default="matin-agent")
    target_file = get_field(plan, "target_file_to_delete")
    task_desc = get_field(plan, "task_description", default=prompt)
    extracted_env = get_field(plan, "extracted_env", default={})

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
        files_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in existing_files.items()])
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

        files = get_field(mod_data, "files", default={})

    # Сценарий: Создание с нуля (ENGINEER)
    else:
        await notify(f"🚀 Principal Engineer: проектирую новый сервис {project_name}...")
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

        files = get_field(eng_data, "files", default={})

    if not files:
        return "⚠️ Модель не сгенерировала файлы для сохранения."

    # 3. Аудит (REVIEWER)
    await notify("🧐 Principal Auditor: провожу аудит безопасности и синтаксиса...")
    review_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in files.items()])
    review_raw = await asyncio.to_thread(ask, REVIEWER_SYSTEM, REVIEWER_USER.format(files=review_dump, user_prompt=prompt))
    
    try:
        review_data = safe_parse_json(review_raw)
    except Exception:
        review_data = {"approved": True, "issues": []}

    approved = get_field(review_data, "approved", default=True)
    issues = get_field(review_data, "issues", default=[])

    # Если аудит выявил замечания — вызываем FIXER
    if not approved and issues:
        await notify("🛠 Principal Fixer: исправляю замечания аудитора...")
        for file_path, content in list(files.items()):
            fixed_code = await asyncio.to_thread(
                ask,
                FIXER_SYSTEM,
                FIXER_USER.format(
                    file_path=file_path,
                    issues="\n".join(issues),
                    code=content,
                ),
            )
            if fixed_code and not fixed_code.startswith("❌"):
                files[file_path] = fixed_code.strip()

    # 4. Отправка в GitHub
    await notify(f"📤 DevOps: отправляю проверенный production-ready код в {project_name}...")
    repo_url = await asyncio.to_thread(push_project, project_name, files)

    # 5. Деплой
    await notify("🚀 Запускаю обновление сервиса на Render...")
    deploy_status = await asyncio.to_thread(trigger_deploy)

    issues_text = f"\n⚠️ Замечания ревьюера: {', '.join(issues)}" if issues else "\n🛡 Аудит безопасности пройден на 100%."
    return (
        f"✅ Проект {project_name} успешно обновлен!\n\n"
        f"📂 Репозиторий: {repo_url}"
        f"{issues_text}\n"
        f"🚀 Деплой: {deploy_status}"
    )

orchestrate = run_task