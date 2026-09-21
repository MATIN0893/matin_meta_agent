import json
import re
from core.llm_client import ask
from core.prompts import (
    PLANNER_SYSTEM,
    ENGINEER_SYSTEM,
    MODIFIER_SYSTEM,
    REVIEWER_SYSTEM,
    FIXER_SYSTEM,
)
from services.sandbox_service import validate_project_files
from services.github_service import (
    list_user_repos,
    get_repo_files,
    push_project,
    delete_repo_file,
    delete_repo,
)
from services.deploy_service import trigger_deploy

MAX_FIX_ATTEMPTS = 3

def clean_key(k: str) -> str:
    """Удаляет пробелы, переносы строк и любые кавычки из имени ключа."""
    return str(k).replace('"', '').replace("'", '').strip()

def normalize_dict(obj):
    """Рекурсивно очищает все ключи словаря от невидимого мусора."""
    if isinstance(obj, dict):
        return {clean_key(k): normalize_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [normalize_dict(elem) for elem in obj]
    return obj

def extract_json(text: str) -> dict:
    clean = re.sub(r"```(?:json)?", "", text).strip()
    
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1:
        clean = clean[start : end + 1]

    # Убираем запятые перед закрывающими скобками
    clean = re.sub(r",\s*([\]}])", r"\1", clean)

    data = json.loads(clean)
    return normalize_dict(data)

def get_field(data: dict, target: str, default=None):
    """Безопасный поиск поля независимо от регистра или пробелов."""
    target_clean = clean_key(target).lower()
    for k, v in data.items():
        if clean_key(k).lower() == target_clean:
            return v
    return default

async def run_task(user_prompt: str, progress_cb=None) -> str:
    try:
        if progress_cb:
            await progress_cb("🧠 Анализирую задачу и репозитории...")

        # 1. Получаем список существующих репозиториев
        try:
            repos = list_user_repos(limit=20)
        except Exception:
            repos = []

        repo_list_str = ", ".join(repos) if repos else "Нет существующих репозиториев"

        # 2. Планирование и извлечение секретов/намерения
        planner_prompt = PLANNER_SYSTEM.format(repo_list=repo_list_str)
        plan_raw = ask(planner_prompt, user_prompt)
        plan = extract_json(plan_raw)

        action = get_field(plan, "action", "create")
        project_name = get_field(plan, "project_name", "my_service")
        task_desc = get_field(plan, "task_description", user_prompt)
        target_file_to_delete = get_field(plan, "target_file_to_delete", None)
        extracted_env = get_field(plan, "extracted_env", {})

        # === СЦЕНАРИЙ 1: УДАЛЕНИЕ ===
        if action == "delete":
            if target_file_to_delete:
                if progress_cb:
                    await progress_cb(f"🗑 Удаляю файл {target_file_to_delete}...")
                ok = delete_repo_file(project_name, target_file_to_delete)
                return f"✅ Файл <code>{target_file_to_delete}</code> удален из <code>{project_name}</code>." if ok else f"❌ Не удалось удалить файл <code>{target_file_to_delete}</code>."
            else:
                if progress_cb:
                    await progress_cb(f"⚠️ Удаляю репозиторий {project_name}...")
                ok = delete_repo(project_name)
                return f"✅ Репозиторий <code>{project_name}</code> полностью удален с GitHub." if ok else f"❌ Ошибка удаления репозитория <code>{project_name}</code>."

        # === СЦЕНАРИЙ 2: МОДИФИКАЦИЯ СУЩЕСТВУЮЩЕГО РЕПОЗИТОРИЯ ===
        elif action == "modify" and project_name in repos:
            if progress_cb:
                await progress_cb(f"📂 Скачиваю проект <code>{project_name}</code> с GitHub...")
            current_files = get_repo_files(project_name)

            if progress_cb:
                await progress_cb("🛠 Модифицирую код проекта...")
            
            context = f"Проект: {project_name}\n"
            if extracted_env:
                context += f"Перехваченные секреты: {json.dumps(extracted_env, ensure_ascii=False)}\n"
            context += f"Задача: {task_desc}\n\nТекущие файлы проекта:\n"
            for p, content in current_files.items():
                context += f"--- {p} ---\n{content}\n"

            mod_raw = ask(MODIFIER_SYSTEM, context)
            mod_data = extract_json(mod_raw)
            files_to_update = get_field(mod_data, "files", {})

            # Проверяем синтаксис
            for attempt in range(MAX_FIX_ATTEMPTS):
                errors = validate_project_files(files_to_update)
                if not errors:
                    break
                if progress_cb:
                    await progress_cb(f"🔧 Исправляю синтаксис (попытка {attempt+1})...")
                for fn, err in errors.items():
                    fixed = ask(FIXER_SYSTEM, f"Файл: {fn}\nОшибка: {err}\nКод:\n{files_to_update[fn]}")
                    files_to_update[fn] = fixed

            if progress_cb:
                await progress_cb(f"📦 Пушу изменения в <code>{project_name}</code>...")
            repo_url = push_project(project_name, files_to_update)

            if progress_cb:
                await progress_cb("🚀 Перезапускаю деплой на Render...")
            deploy_status = trigger_deploy()

            return (
                f"✅ <b>Проект обновлен!</b>\n"
                f"Репозиторий: <code>{project_name}</code>\n"
                f"Изменено файлов: {len(files_to_update)}\n"
                f"GitHub: {repo_url}\n"
                f"Render: {deploy_status}"
            )

        # === СЦЕНАРИЙ 3: СОЗДАНИЕ С НУЛЯ ===
        else:
            if progress_cb:
                await progress_cb(f"🏗 Проектирую новую архитектуру для <code>{project_name}</code>...")

            prompt_for_eng = f"Задача: {task_desc}\nПроект: {project_name}\n"
            if extracted_env:
                prompt_for_eng += f"Перехваченные секреты: {json.dumps(extracted_env, ensure_ascii=False)}\n"

            eng_raw = ask(ENGINEER_SYSTEM, prompt_for_eng)
            eng_data = extract_json(eng_raw)
            files = get_field(eng_data, "files", {})

            # Исправление синтаксических ошибок
            for attempt in range(MAX_FIX_ATTEMPTS):
                errors = validate_project_files(files)
                if not errors:
                    break
                if progress_cb:
                    await progress_cb(f"🔧 Исправляю ошибки кода ({attempt+1})...")
                for fn, err in errors.items():
                    fixed = ask(FIXER_SYSTEM, f"Файл: {fn}\nОшибка: {err}\nКод:\n{files[fn]}")
                    files[fn] = fixed

            if progress_cb:
                await progress_cb("📦 Создаю репозиторий и пушу код на GitHub...")
            repo_url = push_project(project_name, files)

            if progress_cb:
                await progress_cb("🚀 Запускаю деплой на Render...")
            deploy_status = trigger_deploy()

            env_report = ""
            if extracted_env:
                env_keys = ", ".join([f"<code>{k}</code>" for k in extracted_env.keys()])
                env_report = f"\n🔐 Секреты перехвачены и добавлены в конфиг: {env_keys}"

            return (
                f"✅ <b>Проект создан с нуля!</b>\n"
                f"Репозиторий: <code>{project_name}</code>\n"
                f"Файлов создано: {len(files)}\n"
                f"GitHub: {repo_url}\n"
                f"Render: {deploy_status}"
                f"{env_report}"
            )

    except json.JSONDecodeError as e:
        return f"❌ Ошибка разбора JSON от модели: {e}"
    except Exception as e:
        return f"❌ Критическая ошибка: {e}"