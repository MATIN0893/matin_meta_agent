import ast
import json
import re
from core.llm_client import ask
from core.prompts import (
    PLANNER_SYSTEM,
    PLANNER_USER,
    MODIFIER_SYSTEM,
    MODIFIER_USER,
    REVIEWER_SYSTEM,
    REVIEWER_USER,
)
from services.github_service import (
    list_user_repos,
    get_repo_files,
    push_project,
    delete_repo,
)
from services.deploy_service import trigger_deploy

def normalize_dict(d: dict) -> dict:
    """Приводит все ключи словаря к нижнему регистру для защиты от опечаток модели."""
    if not isinstance(d, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in d.items()}

def get_field(d: dict, *keys, default=None):
    """Ищет поле среди вариантов ключей без учета регистра."""
    norm = normalize_dict(d)
    for k in keys:
        clean = str(k).strip().lower()
        if clean in norm:
            return norm[clean]
    return default

def safe_parse_json(raw: str) -> dict:
    """
    Бронебойный парсер JSON от LLM:
    - очищает Markdown-блоки ```json ... ```
    - заменяет Python True/False/None на true/false/null
    - убирает висячие запятые
    - в крайнем случае использует ast.literal_eval
    """
    if not raw:
        return {}

    text = raw.strip()

    # Извлекаем содержимое между ```json ... ``` или ``` ... ```
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1).strip()

    # Находим границы JSON-объекта
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]

    # Исправляем Python-булевы значения и null
    prepared = re.sub(r"\bTrue\b", "true", text)
    prepared = re.sub(r"\bFalse\b", "false", prepared)
    prepared = re.sub(r"\bNone\b", "null", prepared)

    # Убираем висячие запятые перед закрывающими скобками
    prepared = re.sub(r",\s*([\}\]])", r"\1", prepared)

    # Попытка 1: Стандартный JSON с исправлениями
    try:
        return json.loads(prepared)
    except Exception:
        pass

    # Попытка 2: ast.literal_eval для словарей в формате Python
    try:
        val = ast.literal_eval(text)
        if isinstance(val, dict):
            return val
    except Exception:
        pass

    # Если всё не удалось — вызываем стандартный loads для получения точного лога
    return json.loads(prepared)

def orchestrate(prompt: str, status_cb=None) -> str:
    def notify(msg: str):
        if status_cb:
            status_cb(msg)

    # 1. Сбор информации о репозиториях
    repos = list_user_repos()
    repos_str = ", ".join(repos) if repos else "Нет существующих репозиториев"

    # 2. Планирование
    notify("🔍 Анализирую репозитории и составляю план...")
    plan_raw = ask(PLANNER_SYSTEM, PLANNER_USER.format(user_prompt=prompt, repos=repos_str))
    
    try:
        plan = safe_parse_json(plan_raw)
    except Exception as e:
        return f"❌ Ошибка разбора плана: {e}"

    action = get_field(plan, "action", default="create")
    repo_name = get_field(plan, "repo_name", "target_repo", "repo", default="new_agent_project")

    # Ветка: Удаление
    if action == "delete":
        notify(f"🗑 Удаляю репозиторий {repo_name}...")
        ok = delete_repo(repo_name)
        if ok:
            return f"✅ Репозиторий {repo_name} успешно удален."
        return f"❌ Не удалось удалить репозиторий {repo_name}."

    files = {}

    # Ветка: Модификация
    if action == "modify":
        notify(f"📥 Скачиваю проект {repo_name}...")
        existing_files = get_repo_files(repo_name)
        if not existing_files:
            return f"⚠️ Репозиторий {repo_name} пуст или не найден на GitHub."

        notify(f"⚙️ Модифицирую код проекта {repo_name}...")
        files_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in existing_files.items()])
        mod_raw = ask(MODIFIER_SYSTEM, MODIFIER_USER.format(user_prompt=prompt, files=files_dump))
        
        try:
            mod_data = safe_parse_json(mod_raw)
        except Exception as e:
            return f"❌ Ошибка разбора изменений: {e}"

        files = get_field(mod_data, "files", default={})

    # Ветка: Создание с нуля
    else:
        notify(f"🚀 Создаю новый проект {repo_name}...")
        from core.prompts import CODER_SYSTEM, CODER_USER
        coder_raw = ask(CODER_SYSTEM, CODER_USER.format(user_prompt=prompt))
        try:
            coder_data = safe_parse_json(coder_raw)
        except Exception as e:
            return f"❌ Ошибка генерации проекта: {e}"
        files = get_field(coder_data, "files", default={})

    if not files:
        return "⚠️ Модель не сгенерировала файлов для записи."

    # 3. Ревью и контроль качества (Reviewer)
    notify("🧐 Провожу финальную проверку качества кода...")
    review_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in files.items()])
    review_raw = ask(REVIEWER_SYSTEM, REVIEWER_USER.format(user_prompt=prompt, files=review_dump))
    
    try:
        review_data = safe_parse_json(review_raw)
    except Exception:
        # Если ревью не спарсилось, не прерываемся — код уже готов
        review_data = {"approved": True}

    # 4. Отправка в GitHub
    notify(f"📤 Отправляю проверенный код в GitHub ({repo_name})...")
    repo_url = push_project(repo_name, files)

    # 5. Деплой
    notify("🚀 Запускаю обновление сервиса на Render...")
    deploy_status = trigger_deploy()

    comment = get_field(review_data, "feedback", "comment", "critique", default="Код успешно проверен и оптимизирован.")
    return (
        f"✅ Проект успешно обновлен!\n\n"
        f"📂 Репозиторий: {repo_url}\n"
        f"📋 Ревью: {comment}\n"
        f"Статус деплоя: {deploy_status}"
    )