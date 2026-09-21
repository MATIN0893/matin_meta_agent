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

def extract_json(text: str) -> dict:
    # 1. Убираем markdown-теги ```json и ```
    clean = re.sub(r"```(?:json)?", "", text).strip()
    
    # 2. Вырезаем только тело JSON от первой { до последней }
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1:
        clean = clean[start : end + 1]

    # 3. Нормализуем пробелы и переносы строк вокруг ключей
    clean = re.sub(r'[\r\n]+\s*"', '"', clean)
    clean = re.sub(r",\s*([\]}])", r"\1", clean)

    try:
        data = json.loads(clean)
    except Exception:
        # Резервный разбор через строгий regex если стандартный json упал
        import ast
        try:
            data = ast.literal_eval(clean)
        except Exception:
            raise json.JSONDecodeError("Не удалось распарсить JSON", clean, 0)

    if isinstance(data, dict):
        # Очищаем все ключи от лишних пробелов, кавычек и спецсимволов
        cleaned_dict = {}
        for k, v in data.items():
            key_clean = str(k).strip().strip('"').strip("'").strip()
            cleaned_dict[key_clean] = v
        return cleaned_dict

    return data

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

        action = plan.get("action", "create")
        project_name = plan.get("project_name", "my_service")
        task_desc = plan.get("task_description", user_prompt)
        target_file_to_delete = plan.get("target_file_to_delete")
        extracted_env = plan.get("extracted_env", {})

        # === СЦЕНАРИЙ 1: УДАЛЕНИЕ ===
        if action == "delete":
            if target_file_to_delete:
                if progress_cb:
                    await progress_cb(f"🗑 Удаляю файл {target_file_to_delete}...")
                ok = delete_repo_file(project_name, target_file_to_delete)
                return f"✅ Файл `{target_file_to_delete}` удален из `{project_name}`." if ok else f"❌ Не удалось удалить файл `{target_file_to_delete}`."
            else:
                if progress_cb:
                    await progress_cb(f"⚠️ Удаляю репозиторий {project_name}...")
                ok = delete_repo(project_name)
                return f"✅ Репозиторий `{project_name}` полностью удален с GitHub." if ok else f"❌ Ошибка удаления репозитория `{project_name}`."

        # === СЦЕНАРИЙ 2: МОДИФИКАЦИЯ СУЩЕСТВУЮЩЕГО РЕПОЗИТОРИЯ ===
        elif action == "modify" and project_name in repos:
            if progress_cb:
                await progress_cb(f"📂 Скачиваю проект `{project_name}` с GitHub...")
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
            files_to_update = mod_data.get("files", {})

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
                await progress_cb(f"📦 Пушу изменения в `{project_name}`...")
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
                await progress_cb(f"🏗 Проектирую новую архитектуру для `{project_name}`...")

            prompt_for_eng = f"Задача: {task_desc}\nПроект: {project_name}\n"
            if extracted_env:
                prompt_for_eng += f"Перехваченные секреты: {json.dumps(extracted_env, ensure_ascii=False)}\n"

            eng_raw = ask(ENGINEER_SYSTEM, prompt_for_eng)
            eng_data = extract_json(eng_raw)
            files = eng_data.get("files", {})

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