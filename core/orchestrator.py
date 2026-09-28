import ast
import asyncio
import inspect
import json
import logging
import re
import time
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
import services.github_service as github_service
from services.github_service import (
    list_user_repos,
    get_repo_files,
    push_project,
    delete_repo,
    delete_repo_file,
    get_user_repositories,
    find_matching_repo,
    find_matching_repo_name,
    format_repositories_list,
    is_repo_list_intent,
    check_repo_exists,
    get_repo_metadata,
    get_repo_tree,
    get_repo_files_list,
    get_repo_file_content,
    search_repo_files,
    trace_execution_path,
)
from config.settings import GITHUB_USERNAME
from core.security import security_guard, Permission
from core.memory import memory
from core.task_engine import task_engine, TaskState
from engine.agent_registry import agent_registry, AgentLifecycle

logger = logging.getLogger("MATIN.ORCHESTRATOR")

try:
    from services.patrol_service import record_brain_success
except ImportError:
    def record_brain_success():
        pass

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None

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
    return code.strip()


def extract_any_code_snippet(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    matches = re.findall(r'```(?:[a-zA-Z0-9_\-]+)?\s*\n(.*?)```', text, re.DOTALL)
    if matches:
        longest = max(matches, key=len).strip()
        if len(longest) > 10:
            return longest
    return ""


def extract_regex_fields(text: str) -> dict:
    result = {}

    for field in ["action", "project_name", "target_file", "file_path", "status"]:
        m = re.search(rf'\"{field}\"\s*:\s*\"([^\"]*)\"', text, re.IGNORECASE)
        if m:
            result[field] = m.group(1).strip()

    for field in ["code", "task_description", "plan", "explanation"]:
        m = re.search(rf'\"{field}\"\s*:\s*\"(.*?)\"(?=\s*,\s*\"[a-zA-Z_]+\"|\s*}})', text, re.DOTALL)
        if m:
            val = m.group(1)
            val = val.replace('\\"', '"').replace('\\\\', '\\')
            result[field] = val

    files = {}
    files_block_match = re.search(r'\"files\"\s*:\s*\{(.*?)\}(?=\s*,\s*\"[a-zA-Z_]+\"|\s*$|\s*\})', text, re.DOTALL)
    block = files_block_match.group(1) if files_block_match else text

    file_matches = re.finditer(r'\"([a-zA-Z0-9_./\-]+)\"\s*:\s*\"(.*?)\"(?=\s*,\s*\"[a-zA-Z0-9_./\-]+\"|\s*$|\s*\})', block, re.DOTALL)
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
    if not text or not isinstance(text, str):
        return {}
    cleaned = text.strip()

    code_match = re.search(r"```(?:json|python)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    candidate = code_match.group(1).strip() if code_match else cleaned

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

    extracted = extract_regex_fields(candidate)
    if extracted:
        return extracted

    obj_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if obj_match:
        try:
            data = json.loads(obj_match.group(1).strip(), strict=False)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


def collect_project_files(parsed_data: dict, raw_text: str = "", default_target: str = "", existing_files: dict = None) -> dict:
    extracted = {}

    if isinstance(parsed_data, dict):
        for key in ["files", "modified_files", "updated_files", "new_files", "changed_files", "source_files", "code_files"]:
            val = parsed_data.get(key)
            if isinstance(val, dict):
                for fpath, fcontent in val.items():
                    if isinstance(fcontent, str) and fcontent.strip():
                        extracted[fpath.strip()] = clean_code_snippet(fcontent)
                    elif isinstance(fcontent, dict) and "content" in fcontent:
                        extracted[fpath.strip()] = clean_code_snippet(str(fcontent["content"]))
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        fpath = item.get("path") or item.get("file_path") or item.get("filename") or item.get("name")
                        fcontent = item.get("content") or item.get("code") or item.get("text")
                        if fpath and fcontent and isinstance(fcontent, str):
                            extracted[str(fpath).strip()] = clean_code_snippet(fcontent)

        if not extracted:
            fpath = parsed_data.get("file_path") or parsed_data.get("target_file") or parsed_data.get("filename") or parsed_data.get("file")
            code = parsed_data.get("code") or parsed_data.get("content")
            if fpath and code and isinstance(code, str):
                extracted[str(fpath).strip()] = clean_code_snippet(code)

    if not extracted and raw_text:
        try:
            p1 = re.compile(r"""```(?:python|py|json|sh|bash)?(?::|\s+title=['"]?|\s+file=['"]?|\s+filename=['"]?|\s+path=['"]?|\s+)([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9_]+)['"]?\s*\n(.*?)```""", re.DOTALL)
            for m in p1.finditer(raw_text):
                extracted[m.group(1).strip()] = clean_code_snippet(m.group(2))
        except Exception as e:
            logger.warning(f"Ошибка в regex p1: {e}")

        try:
            p2 = re.compile(r"""```(?:[a-zA-Z0-9_\-]+)?\s*\n\s*(?:#|//|--)\s*(?:file(?:path|name)?|path):\s*([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9_]+)\s*\n(.*?)```""", re.DOTALL)
            for m in p2.finditer(raw_text):
                extracted[m.group(1).strip()] = clean_code_snippet(m.group(2))
        except Exception as e:
            logger.warning(f"Ошибка в regex p2: {e}")

        try:
            p3 = re.compile(r"""===\s*([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9_]+)\s*===\s*\n(.*?)(?=(?:===\s*[a-zA-Z0-9_./\-]+\.[a-zA-Z0-9_]+)|\Z)""", re.DOTALL)
            for m in p3.finditer(raw_text):
                extracted[m.group(1).strip()] = clean_code_snippet(m.group(2))
        except Exception as e:
            logger.warning(f"Ошибка в regex p3: {e}")

        try:
            p4 = re.compile(r"""#{1,4}\s*[`'"]?([a-zA-Z0-9_./\-]+\.[a-zA-Z0-9_]+)[`'"]?\s*\n\s*```(?:[a-zA-Z0-9_\-]+)?\s*\n(.*?)```""", re.DOTALL)
            for m in p4.finditer(raw_text):
                extracted[m.group(1).strip()] = clean_code_snippet(m.group(2))
        except Exception as e:
            logger.warning(f"Ошибка в regex p4: {e}")

    if not extracted:
        candidate_code = ""
        if isinstance(parsed_data, dict):
            candidate_code = parsed_data.get("code") or parsed_data.get("content") or ""
        if not candidate_code and raw_text:
            candidate_code = extract_any_code_snippet(raw_text)

        if candidate_code and isinstance(candidate_code, str) and candidate_code.strip():
            target_path = default_target
            if not target_path and existing_files:
                py_files = [f for f in existing_files.keys() if f.endswith(".py")]
                if "main.py" in existing_files:
                    target_path = "main.py"
                elif len(py_files) == 1:
                    target_path = py_files[0]
                elif len(existing_files) == 1:
                    target_path = list(existing_files.keys())[0]

            if not target_path:
                target_path = "main.py"

            extracted[target_path] = clean_code_snippet(candidate_code)

    return extracted


def get_field(data, key: str, default=None):
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def classify_task_intent(prompt: str, plan_data: dict = None) -> str:
    """
    Разделяет задачи на категории:
    - CODE_DIAGNOSTIC: поиск источников ошибок (429 Too Many Requests и т.д.), трассировка execution path, поиск символов/строк без изменения кода.
    - REPOSITORY_INSPECTION: показать структуру/метаданные/дерево файлов без глубокой диагностики.
    - CODE_MODIFICATION: изменение кодовой базы и коммит в GitHub.
    - PROJECT_GENERATION: создание проекта с нуля.
    - DELETE: удаление проекта/файла.
    - LIST_REPOS: запрос списка репозиториев.
    """
    if not prompt or not isinstance(prompt, str):
        return "CODE_DIAGNOSTIC"

    prompt_lower = prompt.lower()
    plan_action = (plan_data.get("action", "") if isinstance(plan_data, dict) else "") or ""
    plan_action = plan_action.lower()
    plan_type = (plan_data.get("task_type", "") if isinstance(plan_data, dict) else "") or ""
    plan_type = plan_type.upper()

    # 1. Список всех репозиториев
    if is_repo_list_intent(prompt) or plan_type == "LIST_REPOS" or plan_action == "list":
        return "LIST_REPOS"

    # 2. Удаление
    if re.search(r"^(?:удали|удалить)\s+(?:репозиторий|проект|файл)", prompt_lower, re.I) or plan_type == "DELETE" or plan_action == "delete":
        return "DELETE"

    # 3. Признаки модификации
    write_keywords = [
        "измени", "исправь", "перепиши", "добавь", "создай", "сгенерируй",
        "закоммить", "пуш", "modify", "fix", "update", "create", "patch",
        "write", "commit", "push"
    ]
    no_modify_keywords = [
        "ничего не изменяй", "без изменений", "не изменяй", "не меняй", "read-only",
        "только найди", "только диагностика", "только анализ", "do not modify",
        "dont modify", "don't modify", "no changes"
    ]

    explicit_no_modify = any(k in prompt_lower for k in no_modify_keywords)
    has_write = any(k in prompt_lower for k in write_keywords) and not explicit_no_modify

    # 4. Признаки диагностики
    diagnostic_keywords = [
        "источник ошибки", "ошибка", "ошибки", "429", "too many requests",
        "найди", "упоминани", "почему", "проследи", "трассируй", "трассировк",
        "trace", "search", "диагностик", "debug", "дебаг", "баг", "проблема",
        "execution path", "стек вызовов", "где вызывается", "openrouter",
        "groq", "fallback", "retry", "где", "как устроен"
    ]
    has_diagnostic = any(k in prompt_lower for k in diagnostic_keywords)

    # 5. Признаки простой инспекции структуры/файлов
    inspection_keywords = [
        "структура", "какие файлы", "список файлов", "инспекция",
        "покажи репозиторий", "обзор проекта", "обзор репозитория"
    ]
    has_inspection = any(k in prompt_lower for k in inspection_keywords)

    if plan_type == "CODE_DIAGNOSTIC" or plan_action in ("diagnostic", "debug", "trace") or (has_diagnostic and not has_write):
        return "CODE_DIAGNOSTIC"

    if has_write:
        return "PROJECT_GENERATION" if ("создай" in prompt_lower or plan_type == "PROJECT_GENERATION") else "CODE_MODIFICATION"

    if plan_type == "REPOSITORY_INSPECTION" or plan_action in ("inspect", "inspection") or has_inspection:
        return "REPOSITORY_INSPECTION"

    if explicit_no_modify or not has_write:
        if any(q in prompt_lower for q in ["как", "что", "где", "почему", "найди"]):
            return "CODE_DIAGNOSTIC"
        return "REPOSITORY_INSPECTION"

    return "CODE_MODIFICATION"


async def run_task(prompt: str, status_cb=None) -> str:
    task = task_engine.create_task(user_id=0, command=prompt)

    async def notify(msg: str):
        if status_cb:
            try:
                if inspect.iscoroutinefunction(status_cb):
                    await status_cb(msg)
                else:
                    status_cb(msg)
            except Exception:
                pass

    if is_repo_list_intent(prompt):
        await notify("📁 Запрашиваю список репозиториев с GitHub...")
        repos_data = await asyncio.to_thread(github_service.get_user_repositories)
        result = format_repositories_list(repos_data)
        task_engine.update_state(task.task_id, TaskState.COMPLETED, result=result)
        return result

    task_engine.update_state(task.task_id, TaskState.PLANNING)
    await notify("🧠 Анализирую задачу и составляю план...")
    user_repos = await asyncio.to_thread(github_service.list_user_repos)
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
        err_msg = f"❌ Ошибка разбора плана: {e}"
        task_engine.update_state(task.task_id, TaskState.FAILED, error=err_msg)
        return err_msg

    if not plan:
        err_msg = "❌ Ошибка разбора плана: модель вернула некорректный ответ."
        task_engine.update_state(task.task_id, TaskState.FAILED, error=err_msg)
        return err_msg

    record_brain_success()

    action = get_field(plan, "action", default="modify")
    project_name = get_field(plan, "project_name", default="matin-agent")
    target_file = get_field(plan, "target_file", default="")
    task_desc = get_field(plan, "task_description", default=prompt)
    extracted_env = get_field(plan, "env", default={})

    task.plan = plan
    task.log_action("planned", details=f"action={action} project={project_name}")

    if action in ("list", "list_repos", "repos", "list_projects"):
        await notify("📁 Запрашиваю список репозиториев с GitHub...")
        repos_data = await asyncio.to_thread(github_service.get_user_repositories)
        result = format_repositories_list(repos_data)
        task_engine.update_state(task.task_id, TaskState.COMPLETED, result=result)
        return result

    owner_name = (GITHUB_USERNAME or "MATIN0893").strip().lower()
    if project_name and project_name.strip().lower() in (owner_name, "matin0893"):
        repos_data = await asyncio.to_thread(github_service.get_user_repositories)
        exact_repo_exists = any(r.get("name", "").lower() == project_name.strip().lower() for r in repos_data)
        if not exact_repo_exists:
            if is_repo_list_intent(prompt) or any(w in prompt.lower() for w in ["репозитор", "проект", "repo", "список"]):
                result = format_repositories_list(repos_data)
                task_engine.update_state(task.task_id, TaskState.COMPLETED, result=result)
                return result
            else:
                msg = (
                    f"⚠️ `{project_name}` — это имя профиля GitHub, а не конкретный репозиторий.\n\n"
                    f"{format_repositories_list(repos_data)}\n\n"
                    f"Пожалуйста, уточни имя проекта."
                )
                task_engine.update_state(task.task_id, TaskState.FAILED, error=msg)
                return msg

    if action == "delete":
        matched_del = github_service.find_matching_repo_name(project_name)
        if matched_del and matched_del != project_name:
            project_name = matched_del

        if target_file:
            await notify(f"🗑 Удаляю файл {target_file} в репозитории {project_name}...")
            ok = await asyncio.to_thread(github_service.delete_repo_file, project_name, target_file)
            res = f"✅ Файл {target_file} удален." if ok else f"❌ Не удалось удалить файл {target_file}."
        else:
            await notify(f"🗑 Удаляю репозиторий {project_name}...")
            ok = await asyncio.to_thread(github_service.delete_repo, project_name)
            res = f"✅ Репозиторий {project_name} успешно удален." if ok else f"❌ Не удалось удалить репозиторий {project_name}."

        task_engine.update_state(task.task_id, TaskState.COMPLETED if ok else TaskState.FAILED, result=res)
        return res

    # -------------------------------------------------------------
    # TASK INTENT CLASSIFICATION:
    # REPOSITORY_INSPECTION | CODE_DIAGNOSTIC | CODE_MODIFICATION
    # -------------------------------------------------------------
    task_type = classify_task_intent(prompt, plan)
    task.plan["task_type"] = task_type
    task.plan["original_intent"] = prompt
    task.log_action("classified", details=f"task_type={task_type} project={project_name}")

    matched_repo = github_service.find_matching_repo_name(project_name)
    if matched_repo and matched_repo != project_name:
        await notify(f"🔍 Репозиторий '{project_name}' определен как '{matched_repo}' (fuzzy match)...")
        project_name = matched_repo

    # =============================================================
    # PIPELINE: CODE_DIAGNOSTIC
    # UNDERSTAND -> REPOSITORY_CONTEXT -> READ_FILES -> SEARCH -> TRACE -> ANALYZE -> REPORT
    # FORBIDDEN: MODIFY, COMMIT, PUSH
    # =============================================================
    if task_type == "CODE_DIAGNOSTIC":
        task_engine.update_state(task.task_id, TaskState.EXECUTING)

        # 1. UNDERSTAND: Сохраняем исходный user intent
        task.log_action("understand", details=f"Original intent preserved: '{prompt}'")
        await notify("🧠 **[1/6 UNDERSTAND]** Фиксирую цель диагностики и ограничения (READ-ONLY, без изменений кода)...")

        # Формируем список поисковых запросов
        search_terms = plan.get("search_queries") or []
        if not isinstance(search_terms, list):
            search_terms = []
        for kw in ["429", "too many requests", "openrouter", "api/v1/chat/completions", "groq", "fallback", "retry", "ratelimit", "rate_limit"]:
            if kw.lower() in prompt.lower() and kw not in search_terms:
                search_terms.append(kw)
        if not search_terms:
            search_terms = ["429", "openrouter", "groq", "fallback", "retry"]

        # 2. REPOSITORY_CONTEXT: Метаданные и структура
        task.log_action("repository_context", details=f"Fetching context for {project_name}")
        await notify(f"📁 **[2/6 REPOSITORY_CONTEXT]** Получаю контекст репозитория {project_name}...")

        meta = await asyncio.to_thread(github_service.get_repo_metadata, project_name)
        if not meta.get("success"):
            err_code = meta.get("status_code", 0)
            err_msg = meta.get("error", "Неизвестная ошибка")
            endpoint = meta.get("endpoint", f"https://api.github.com/repos/{project_name}")
            repos_data = await asyncio.to_thread(github_service.get_user_repositories)
            repos_hint = format_repositories_list(repos_data)
            diag = (
                f"❌ **Ошибка доступа к репозиторию `{project_name}`**\n\n"
                f"• **HTTP Status:** `{err_code}`\n"
                f"• **Endpoint:** `{endpoint}`\n"
                f"• **Причина:** {err_msg}\n\n"
                f"{repos_hint}"
            )
            task_engine.update_state(task.task_id, TaskState.FAILED, error=diag)
            return diag

        default_branch = meta.get("default_branch", "main")
        repo_url = meta.get("html_url", f"https://github.com/MATIN0893/{project_name}")
        is_priv = meta.get("private", False)
        vis_icon = "🔒 Приватный" if is_priv else "🌐 Публичный"

        tree_info = await asyncio.to_thread(github_service.get_repo_tree, project_name, default_branch)
        file_list = tree_info.get("files", []) if tree_info.get("success") else await asyncio.to_thread(github_service.get_repo_files_list, project_name, default_branch)

        # 3. READ_FILES: Чтение файлов кодовой базы
        task.log_action("read_files", details=f"Reading files from branch '{default_branch}' ({len(file_list)} files)")
        await notify(f"📥 **[3/6 READ_FILES]** Загружаю файлы кодовой базы для анализа ({len(file_list)} файлов)...")

        repo_files = await asyncio.to_thread(github_service.get_repo_files, project_name, default_branch, 50)

        # 4. SEARCH: Поиск ключевых символов, кодов ошибок, API URL
        task.log_action("search", details=f"Searching terms: {search_terms}")
        await notify(f"🔍 **[4/6 SEARCH]** Сканирую кодовую базу на упоминания: {', '.join(search_terms[:6])}...")

        search_results = github_service.search_repo_files(repo_files, search_terms)

        # 5. TRACE: Трассировка execution path
        task.log_action("trace", details="Tracing execution path: Telegram -> Handler -> LLM Client -> Response")
        await notify("🔗 **[5/6 TRACE]** Трассировка execution path: Telegram → Handler → LLM Client → Error Handling...")

        trace_info = github_service.trace_execution_path(repo_files)

        # 6. ANALYZE: Глубокий инженерный анализ причин
        task.log_action("analyze", details="Analyzing root cause and synthesis")
        await notify("🔬 **[6/6 ANALYZE]** Формирую глубокое инженерное заключение...")

        matches_md = []
        for m in search_results[:30]:
            matches_md.append(f"• `{m['file']}:{m['line']}` [{m['query']}]: `{m['code']}`")
        matches_text = "\n".join(matches_md) if matches_md else "Прямых текстовых совпадений по списку запросов не найдено."

        diagnostic_prompt = (
            "Ты — Senior SRE & Lead Software Architect в MATIN META OS.\n"
            f"Задача пользователя: {prompt}\n\n"
            f"Репозиторий: {project_name} (ветка: {default_branch}, доступ: {vis_icon})\n\n"
            f"ФАКТИЧЕСКИЕ НАЙДЕННЫЕ СОВПАДЕНИЯ В КОДЕ:\n{matches_text}\n\n"
            f"АРХИТЕКТУРНЫЕ СВЯЗИ В КОДЕ (TRACE):\n"
            f"- Telegram Entrypoint: {json.dumps(trace_info.get('telegram_entrypoint', []), ensure_ascii=False)}\n"
            f"- Handlers: {json.dumps(trace_info.get('handlers', []), ensure_ascii=False)}\n"
            f"- LLM вызовы: {json.dumps(trace_info.get('llm_calls', []), ensure_ascii=False)}\n"
            f"- OpenRouter упоминания: {json.dumps(trace_info.get('openrouter_mentions', []), ensure_ascii=False)}\n"
            f"- Groq упоминания: {json.dumps(trace_info.get('groq_mentions', []), ensure_ascii=False)}\n"
            f"- Обработчики Rate Limit (429): {json.dumps(trace_info.get('rate_limit_handlers', []), ensure_ascii=False)}\n\n"
            "СОДЕРЖИМОЕ КЛЮЧЕВЫХ ФАЙЛОВ:\n"
            + "\n\n".join([f"=== {f} ===\n{c[:2500]}" for f, c in repo_files.items() if any(k in f.lower() for k in ["ai", "bot", "main", "config"])])
            + "\n\n"
            "Составь подробный, точный и структурированный инженерный отчет:\n"
            "1. 🎯 Цель диагностики\n"
            "2. 📍 Точный источник ошибки 429 и найденные упоминания (файлы, номера строк, точные фрагменты кода)\n"
            "3. 🔄 Execution Path (пошаговая цепочка: от получения сообщения в Telegram через Handler к вызову LLM и обработке ответа)\n"
            "4. ⚠️ Первопричина ошибки 429 (Too Many Requests)\n"
            "5. 💡 Архитектурные рекомендации по устранению (retry, fallback, backoff) БЕЗ изменения кода\n"
            "6. 🔒 Статус: READ-ONLY (изменения в кодовую базу не вносились)."
        )

        ai_report = await asyncio.to_thread(
            ask,
            "Ты элитный SRE инженер MATIN META. Отвечай строго профессионально, опираясь только на реальный код репозитория.",
            diagnostic_prompt,
        )

        # 7. REPORT: Финальный отчет
        task.log_action("report", details="Diagnostic report delivered")

        final_msg = (
            f"🔍 **ИНЖЕНЕРНАЯ ДИАГНОСТИКА: [{project_name}]({repo_url})**\n\n"
            f"{ai_report.strip()}\n\n"
            "────────────────────────────────────────\n"
            "🛡 **Режим безопасности:** `READ-ONLY (CODE_DIAGNOSTIC)`\n"
            "🚫 **Действия `MODIFY`, `COMMIT`, `PUSH` строго заблокированы.**"
        )
        task_engine.update_state(task.task_id, TaskState.COMPLETED, result=final_msg)
        return final_msg

    # =============================================================
    # PIPELINE: REPOSITORY_INSPECTION
    # Показать структуру/метаданные репозитория или файл БЕЗ изменений
    # =============================================================
    if task_type == "REPOSITORY_INSPECTION":
        task_engine.update_state(task.task_id, TaskState.EXECUTING)
        task.log_action("inspection", details=f"Inspecting repository structure for {project_name}")
        await notify(f"🔍 Инспектирую репозиторий {project_name} (READ-ONLY)...")

        meta = await asyncio.to_thread(github_service.get_repo_metadata, project_name)
        if not meta.get("success"):
            err_code = meta.get("status_code", 0)
            err_msg = meta.get("error", "Неизвестная ошибка")
            endpoint = meta.get("endpoint", f"https://api.github.com/repos/{project_name}")
            repos_data = await asyncio.to_thread(github_service.get_user_repositories)
            repos_hint = format_repositories_list(repos_data)
            diag = (
                f"❌ **Ошибка доступа к репозиторию `{project_name}`**\n\n"
                f"• **HTTP Status:** `{err_code}`\n"
                f"• **Endpoint:** `{endpoint}`\n"
                f"• **Причина:** {err_msg}\n\n"
                f"{repos_hint}"
            )
            task_engine.update_state(task.task_id, TaskState.FAILED, error=diag)
            return diag

        default_branch = meta.get("default_branch", "main")
        repo_url = meta.get("html_url", f"https://github.com/MATIN0893/{project_name}")
        is_priv = meta.get("private", False)
        vis_icon = "🔒 Приватный" if is_priv else "🌐 Публичный"

        if target_file:
            await notify(f"📄 Читаю файл `{target_file}` из ветки `{default_branch}`...")
            file_res = await asyncio.to_thread(github_service.get_repo_file_content, project_name, target_file, default_branch)
            if file_res.get("success"):
                fcontent = file_res.get("content", "")
                ext = target_file.split(".")[-1] if "." in target_file else ""
                lines_cnt = len(fcontent.splitlines())
                preview = fcontent[:3500]
                trunc_note = f"\n\n_... (показано первые 3500 символов из {len(fcontent)})_" if len(fcontent) > 3500 else ""
                ans = (
                    f"📄 **Файл `{target_file}` в [{project_name}]({repo_url})**\n\n"
                    f"• **Ветка:** `{default_branch}` ({vis_icon})\n"
                    f"• **Размер:** `{len(fcontent)} байт` ({lines_cnt} строк)\n\n"
                    f"```{ext}\n{preview}\n```{trunc_note}"
                )
                task_engine.update_state(task.task_id, TaskState.COMPLETED, result=ans)
                return ans
            else:
                err_ans = (
                    f"❌ **Не удалось прочитать файл `{target_file}` в `{project_name}`**\n\n"
                    f"• **HTTP Status:** `{file_res.get('status_code')}`\n"
                    f"• **Endpoint:** `{file_res.get('endpoint')}`\n"
                    f"• **Ветка:** `{default_branch}`\n"
                    f"• **Причина:** {file_res.get('error')}"
                )
                task_engine.update_state(task.task_id, TaskState.FAILED, error=err_ans)
                return err_ans

        await notify(f"📁 Получаю дерево файлов ветки `{default_branch}`...")
        tree_info = await asyncio.to_thread(github_service.get_repo_tree, project_name, default_branch)
        file_list = tree_info.get("files", []) if tree_info.get("success") else await asyncio.to_thread(github_service.get_repo_files_list, project_name, default_branch)

        key_files = await asyncio.to_thread(github_service.get_repo_files, project_name, default_branch, 15)

        files_preview = [f"• `{f}`" for f in file_list[:25]]
        files_md = "\n".join(files_preview)
        if len(file_list) > 25:
            files_md += f"\n• _... и еще {len(file_list) - 25} файлов_"
        elif not file_list:
            files_md = "• _Файлы не обнаружены (репозиторий пуст)_"

        stack_tags = []
        all_texts = " ".join(key_files.values()).lower()
        if "fastapi" in all_texts or "from fastapi" in all_texts or any("fastapi" in f for f in file_list):
            stack_tags.append("FastAPI")
        if "telegram" in all_texts or "telegram.ext" in all_texts or any("bot" in f for f in file_list):
            stack_tags.append("Telegram Bot")
        if "groq" in all_texts or "llama" in all_texts or any("groq" in f for f in file_list):
            stack_tags.append("Groq LLM")
        if "Dockerfile" in file_list or "docker" in all_texts:
            stack_tags.append("Docker")
        if "render.yaml" in file_list:
            stack_tags.append("Render Cloud")
        if not stack_tags:
            stack_tags.append("Python 3")

        desc = meta.get("description") or "Автономный микросервис"

        report_msg = (
            f"🔍 **ИНСПЕКЦИЯ РЕПОЗИТОРИЯ: [{project_name}]({repo_url})**\n\n"
            f"• **Доступ:** {vis_icon}\n"
            f"• **Основная ветка:** `{default_branch}`\n"
            f"• **Стек технологий:** {', '.join(stack_tags)}\n"
            f"• **Всего файлов в git tree:** `{len(file_list)}`\n"
            f"• **Описание:** _{desc}_\n\n"
            f"📂 **Структура файлов ({default_branch}):**\n{files_md}\n\n"
            "✅ _READ-ONLY инспекция завершена успешно. Изменений в репозиторий не вносилось._"
        )
        task_engine.update_state(task.task_id, TaskState.COMPLETED, result=report_msg)
        return report_msg

    # =============================================================
    # PIPELINE: CODE_MODIFICATION / PROJECT_GENERATION
    # Изменение кодовой базы с ревью и деплоем
    # =============================================================
    files = {}
    task_engine.update_state(task.task_id, TaskState.EXECUTING)

    if action == "modify":
        matched_repo = github_service.find_matching_repo_name(project_name)
        if matched_repo and matched_repo != project_name:
            await notify(f"🔍 Репозиторий '{project_name}' найден как '{matched_repo}' (fuzzy match)...")
            project_name = matched_repo

        await notify(f"📥 Скачиваю файлы проекта {project_name}...")
        existing_files = await asyncio.to_thread(github_service.get_repo_files, project_name)
        if not existing_files:
            repos_data = await asyncio.to_thread(github_service.get_user_repositories)
            repos_hint = format_repositories_list(repos_data)
            res = f"⚠️ Репозиторий `{project_name}` пуст или не найден на GitHub.\n\n{repos_hint}"
            task_engine.update_state(task.task_id, TaskState.FAILED, error=res)
            return res

        await notify(f"⚙️ Senior Maintainer: пересобираю кодовую базу {project_name}...")

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
        except Exception:
            mod_data = {}

        files = collect_project_files(
            parsed_data=mod_data,
            raw_text=mod_raw,
            default_target=target_file,
            existing_files=existing_files,
        )
        record_brain_success()

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
        except Exception:
            eng_data = {}

        files = collect_project_files(
            parsed_data=eng_data,
            raw_text=eng_raw,
            default_target=target_file or "main.py",
            existing_files=None,
        )
        record_brain_success()

    if not files:
        res = "⚠️ Не удалось получить файлы для сохранения."
        task_engine.update_state(task.task_id, TaskState.FAILED, error=res)
        return res

    for fname, fcontent in files.items():
        has_secret, labels = security_guard.contains_secrets(fcontent)
        if has_secret:
            await notify(f"⚠️ Security Guard: замаскирован токен в файле `{fname}`")
            files[fname] = security_guard.sanitize_secrets(fcontent)

    task_engine.update_state(task.task_id, TaskState.TESTING)
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

    if status != "APPROVED":
        task_engine.update_state(task.task_id, TaskState.VALIDATING)
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
            try:
                fix_data = safe_parse_json(fix_raw)
            except Exception:
                fix_data = {}

            fix_files = collect_project_files(
                parsed_data=fix_data,
                raw_text=fix_raw,
                default_target=target_file,
                existing_files=files,
            )
            if fix_files:
                files.update(fix_files)
        except Exception:
            pass

    task_engine.update_state(task.task_id, TaskState.DEPLOYING)
    await notify(f"🚀 Загружаю код в GitHub репозиторий {project_name}...")
    repo_url = await asyncio.to_thread(github_service.push_project, project_name, files)

    memory.record_project(
        name=project_name,
        repo=repo_url,
        description=task_desc,
        status="active"
    )

    final_msg = f"✅ Проект {project_name} успешно обновлен и опубликован на GitHub!\n🔗 {repo_url}"
    task_engine.update_state(task.task_id, TaskState.COMPLETED, result=final_msg)
    return final_msg


orchestrate = run_task
