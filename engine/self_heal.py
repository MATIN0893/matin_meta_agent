import ast
import logging
from core.model_router import query_model

logger = logging.getLogger("MATIN.SELF_HEAL")

def validate_python_code(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError as e:
        logger.error(f"Синтаксическая ошибка в патче: {e}")
        return False

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
