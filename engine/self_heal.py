import ast
import re
import os
import logging
from typing import Optional, Dict, Any
from core.model_router import query_model
from services.github_service import get_repo_files, push_project
from services.render_service import restart_service
from core.memory import memory

logger = logging.getLogger("MATIN.SELF_HEAL")


def validate_python_code(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError as e:
        logger.error(f"Синтаксическая ошибка в патче: {e}")
        return False


def extract_failing_file(logs: str) -> str:
    """Извлекает имя сбойного Python-файла из traceback логов."""
    if not logs:
        return "main.py"
    matches = re.findall(r'File\s+["\']([^"\']+\.py)["\']', logs)
    if matches:
        last_file = matches[-1]
        filename = last_file.replace("\\", "/").split("/")[-1]
        return filename
    return "main.py"


def generate_patch(repo_name: str, file_path: str, original_code: str, error_logs: str) -> dict:
    system_prompt = (
        "Ты — автономный SRE инженер MATIN FIX. Твоя задача — исправить ошибку в Python файле.\n"
        "Правила:\n"
        "1. Верни исправленный код файла целиком.\n"
        "2. Не добавляй markdown разметку (никаких ```python), только чистый код.\n"
        "3. Не ломай существующую логику, исправь только причину падения."
    )
    
    prompt = (
        f"Репозиторий: {repo_name}\n"
        f"Файл: {file_path}\n"
        f"Лог ошибки:\n{error_logs[-2000:]}\n\n"
        f"Исходный код:\n{original_code}\n\n"
        "Исправь код:"
    )

    try:
        fixed_code = query_model(prompt=prompt, system_prompt=system_prompt)
        fixed_code = fixed_code.strip()
        if fixed_code.startswith("```"):
            lines = fixed_code.splitlines()
            fixed_code = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        
        is_valid = validate_python_code(fixed_code)
        if not is_valid:
            return {"success": False, "reason": "SYNTAX_CHECK_FAILED"}

        confidence = 0.95 if is_valid and len(fixed_code) > 20 else 0.50

        return {
            "success": True,
            "fixed_code": fixed_code,
            "confidence": confidence,
            "can_auto_commit": confidence >= 0.90
        }
    except Exception as e:
        logger.error(f"Ошибка вызова LLM в Self-Heal: {e}")
        return {"success": False, "reason": str(e)}


def attempt_self_heal(
    srv_name: str,
    render_id: str = "",
    error_logs: str = "",
    bot = None
) -> Dict[str, Any]:
    """
    Полный автоматический конвейер самовосстановления:
    1. Определение упавшего файла из логов.
    2. Скачивание исходного кода из GitHub репозитория.
    3. Генерация патча через LLM.
    4. AST-проверка и синтаксическая валидация.
    5. Коммит и пуш исправлений в репозиторий.
    6. Перезапуск сервиса на Render.
    7. Запись в память.
    """
    logger.info(f"[SELF-HEAL] Запуск восстановления сервиса '{srv_name}'...")

    failing_file = extract_failing_file(error_logs)
    logger.info(f"[SELF-HEAL] Определен сбойный файл: '{failing_file}'")

    # 1. Получаем файлы репозитория
    repo_files = get_repo_files(srv_name)
    if not repo_files:
        msg = f"Репозиторий '{srv_name}' не найден на GitHub или недоступен."
        logger.error(f"[SELF-HEAL] {msg}")
        return {"success": False, "reason": msg}

    target_path = None
    for path in repo_files.keys():
        if path == failing_file or path.endswith(f"/{failing_file}"):
            target_path = path
            break

    if not target_path:
        if "main.py" in repo_files:
            target_path = "main.py"
        elif repo_files:
            target_path = list(repo_files.keys())[0]
        else:
            return {"success": False, "reason": "В репозитории нет файлов для исправления."}

    original_code = repo_files[target_path]

    # 2. Генерируем патч
    patch = generate_patch(
        repo_name=srv_name,
        file_path=target_path,
        original_code=original_code,
        error_logs=error_logs
    )

    if not patch.get("success"):
        reason = patch.get("reason", "Ошибка генерации патча")
        logger.error(f"[SELF-HEAL] Не удалось создать патч для '{srv_name}': {reason}")
        return {"success": False, "reason": reason}

    fixed_code = patch.get("fixed_code", "")

    # 3. Применяем исправление и коммитим в GitHub
    commit_msg = f"fix(self-heal): auto-repair error in {target_path} by SRE Patrol"
    try:
        repo_url = push_project(srv_name, {target_path: fixed_code}, commit_message=commit_msg)
        logger.info(f"[SELF-HEAL] Патч успешно закоммичен в {repo_url}")
    except Exception as e:
        err = f"Ошибка пуша в GitHub: {e}"
        logger.error(f"[SELF-HEAL] {err}")
        return {"success": False, "reason": err}

    # 4. Перезапуск сервиса на Render
    restart_ok = False
    if render_id:
        try:
            restart_service(render_id)
            restart_ok = True
            logger.info(f"[SELF-HEAL] Контейнер {render_id} перезапущен на Render.")
        except Exception as e:
            logger.warning(f"[SELF-HEAL] Ошибка перезапуска сервиса на Render: {e}")

    # 5. Запись в память
    memory.record_bug(
        project=srv_name,
        error=error_logs[:300],
        patch=f"Auto-fixed {target_path}",
        status="resolved"
    )

    return {
        "success": True,
        "target_file": target_path,
        "repo_url": repo_url,
        "restarted": restart_ok
    }
