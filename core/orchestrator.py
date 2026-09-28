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
    from services.patrol_service import record_brain_success
except ImportError:
    def record_brain_success():
        pass

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

try:
    from services.github_service import delete_repo_file
except ImportError:
    def delete_repo_file(repo_name: str, file_path: str):
        return False


JSON_ESCAPE_RULE = """

СТРОГОЕ ПРАВИЛО ФОРМАТИРОВАНИЯ JSON:
- Твой ответ должен быть СТРОГО валидным JSON-объектом без пояснительного текста вокруг и без markdown-тегов.
- Внутри JSON-строк (особенно в коде файлов):
  1. ВСЕ переносы строк ДОЛЖНЫ быть экранированы как \\n (ЗАПРЕЩЕНО вставлять реальные переносы строк внутри строковых значений JSON).
  2. ВСЕ двойные кавычки внутри кода ДОЛЖНЫ быть экранированы как \\\" (или используй одинарные кавычки ' в Python-коде).
  3. ВСЕ обратные слэши должны быть экранированы как \\\\.
- Строго соблюдай синтаксис JSON: никаких висячих запятых, закрывай все скобки и кавычки.
"""


def is_plausible_file_path(path: str) -> bool:
    if not path or " " in path:
        return False
    if path.lower() in {
        "action", "project_name", "target_file", "task_description",
        "code", "files", "env", "file_path", "explanation", "status",
        "plan", "issues", "type", "description"
    }:
        return False
    return "." in path or "/" in path


def clean_code_snippet(code: str) -> str:
    if not isinstance(code, str):
        return ""
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        if len(lines) > 1 and lines[-1].strip().startswith("```"):
            code = "\n".join(lines[1:-1])
        elif len(lines) > 1:
            code = "\n".join(lines[1:])
    return code


def extract_regex_fields(text: str) -> dict:
    result = {}

    # Сканирование простых строковых полей
    for field in ["action", "project_name", "target_file", "file_path", "status"]:
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', text, re.IGNORECASE)
        if m:
            result[field] = m.group(1).strip()

    # Сканирование многострочных полей (код, план, пояснение)
    for field in ["code", "task_description", "plan", "explanation"]:
        m = re.search(rf'"{field}"\s*:\s*"(.*?)"(?=\s*,\s*"[a-zA-Z_]+"|\s*}})', text, re.DOTALL)
        if m:
            val = m.group(1)
            val = val.replace('\\"', '"').replace('\\\\', '\\')
            result[field] = val

    # Сканирование файлов проекта
    files = {}
    files_block_match = re.search(r'"files"\s*:\s*\{(.*?)\}(?=\s*,\s*"[a-zA-Z_]+"|\s*$|\s*\})', text, re.DOTALL)
    block = files_block_match.group(1) if files_block_match else text

    file_matches = re.finditer(r'"([a-zA-Z0-9_./\-]+)"\s*:\s*"(.*?)"(?=\s*,\s*"[a-zA-Z0-9_./\-]+"|\s*$|\s*\})', block, re.DOTALL)
    for fm in file_matches:
        f_name = fm.group(1).strip()
        if is_plausible_file_path(f_name):
            content = fm.group(2).replace('\\"', '"').replace('\\\\', '\\')
            files[f_name] = clean_code_snippet(content)

    if not files and "file_path" in result and "code" in result:
        files[result["file_path"]] = clean_code_snippet(result["code"])

    if files:
        result["files"] = files

    return result


def safe_parse_json(text: str) -> dict:
    """Извлекает и парсит JSON даже при наличии markdown-разметки или неэкранированных строк."""
    if not text or not isinstance(text, str):
        return {}
    cleaned = text.strip()

    # 1. Очистка от markdown-тегов (```json ... ```)
    code_match = re.search(r"```(?:json|python)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    candidate = code_match.group(1).strip() if code_match else cleaned

    # 2. Стандартный json.loads (строгий и нестрогий режим)
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    try:
        data = json.loads(candidate, strict=False)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 3. Подключение json_repair (если доступен)
    if repair_json is not None:
        try:
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
            if isinstance(repaired, str):
                loaded = json.loads(repaired, strict=False)
                if isinstance(loaded, dict):
                    return loaded
        except Exception:
            pass

    # 4. ast.literal_eval для Python-синтаксиса
    try:
        py_cand = candidate
        py_cand = re.sub(r'\btrue\b', 'True', py_cand)
        py_cand = re.sub(r'\bfalse\b', 'False', py_cand)
        py_cand = re.sub(r'\bnull\b', 'None', py_cand)
        res = ast.literal_eval(py_cand)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    # 5. Прямое регулярное извлечение полей без падения
    extracted = extract_regex_fields(candidate)
    if extracted:
        return extracted

    # 6. Поиск любого внешнего JSON-блока { ... }
    obj_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if obj_match:
        try:
            data = json.loads(obj_match.group(1).strip(), strict=False)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


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

    plan_raw = await asyncio.to_thread(
        ask,
        planner_prompt + JSON_ESCAPE_RULE,
        PLANNER_USER.format(user_prompt=prompt),
    )
    try:
        plan = safe_parse_json(plan_raw)
    except Exception as e:
        return f"❌ Ошибка разбора плана: {e}"

    if not plan:
        return "❌ Ошибка разбора плана: модель вернула некорректный ответ."

    record_brain_success()

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
            parts = path.replace('\\\\', '/').split('/')
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
            MODIFIER_SYSTEM + JSON_ESCAPE_RULE,
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
        if not files and get_field(mod_data, "code"):
            target_path = get_field(mod_data, "file_path") or target_file
            if target_path:
                files[target_path] = clean_code_snippet(mod_data["code"])
        record_brain_success()

    # Сценарий: Создание нового проекта (ENGINEER)
    else:
        await notify(f"⚙️ Lead Engineer: генерирую проект {project_name}...")
        eng_raw = await asyncio.to_thread(
            ask,
            ENGINEER_SYSTEM + JSON_ESCAPE_RULE,
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
        if not files and get_field(eng_data, "code"):
            target_path = get_field(eng_data, "file_path") or target_file
            if target_path:
                files[target_path] = clean_code_snippet(eng_data["code"])
        record_brain_success()

    if not files:
        return "⚠️ Не удалось получить файлы для сохранения."

    # Рецензент (REVIEWER)
    await notify("🔍 Code Reviewer: проверяю качество кода...")
    review_dump = "\n\n".join([f"=== {path} ===\n{content}" for path, content in files.items()])
    try:
        review_raw = await asyncio.to_thread(
            ask,
            REVIEWER_SYSTEM + JSON_ESCAPE_RULE,
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
                FIXER_SYSTEM + JSON_ESCAPE_RULE,
                FIXER_USER.format(
                    files=review_dump,
                    issues=json.dumps(review_data.get("issues", []), ensure_ascii=False),
                    user_prompt=prompt,
                ),
            )
            fix_data = safe_parse_json(fix_raw)
            fix_files = fix_data.get("files", {})
            if fix_files:
                files = fix_files
            elif get_field(fix_data, "code"):
                target_path = get_field(fix_data, "file_path") or target_file
                if target_path:
                    files[target_path] = clean_code_snippet(fix_data["code"])
        except Exception:
            pass

    # Пуш в GitHub
    await notify(f"🚀 Загружаю код в GitHub репозиторий {project_name}...")
    repo_url = await asyncio.to_thread(push_project, project_name, files)
    return f"✅ Проект {project_name} успешно обновлен и опубликован на GitHub!\n🔗 {repo_url}"

orchestrate = run_task
