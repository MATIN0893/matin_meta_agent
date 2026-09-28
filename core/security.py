import re
import time
import logging
from enum import Enum
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("MATIN.SECURITY")


class Permission(str, Enum):
    READ = "read"
    SAFE_WRITE = "safe_write"
    RISKY_WRITE = "risky_write"
    DESTRUCTIVE = "destructive"
    PRODUCTION = "production"


SECRET_PATTERNS = [
    (r"ghp_[A-Za-z0-9_]{20,}", "GITHUB_TOKEN"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GITHUB_TOKEN"),
    (r"rnd_[A-Za-z0-9]{20,}", "RENDER_API_KEY"),
    (r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", "TELEGRAM_BOT_TOKEN"),
    (r"gsk_[A-Za-z0-9_]{20,}", "GROQ_API_KEY"),
    (r"sk-or-v1-[A-Za-z0-9_]{20,}", "OPENROUTER_API_KEY"),
    (r"AIza[0-9A-Za-z-_]{35}", "GOOGLE_API_KEY"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "PRIVATE_KEY"),
]

DANGEROUS_COMMANDS = [
    r"\brm\s+(-[rfRF]{1,4}\s+)?(/[a-zA-Z0-9_\-.]*|\*)\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bdrop\s+database\b",
    r"\btruncate\s+table\b",
    r"\bgit\s+push\s+.*--force\b",
]


class SecurityGuard:
    def __init__(self, confirmation_timeout_seconds: int = 300):
        self.pending_confirmations: Dict[int, Dict[str, Any]] = {}
        self.timeout = confirmation_timeout_seconds
        self.audit_log: list = []

    def log_audit(self, action: str, level: Permission, target: str, user_id: int, status: str, details: str = ""):
        entry = {
            "timestamp": time.time(),
            "action": action,
            "level": level.value,
            "target": target,
            "user_id": user_id,
            "status": status,
            "details": details
        }
        self.audit_log.append(entry)
        logger.info(f"[SECURITY AUDIT] user={user_id} level={level.value} action={action} target={target} status={status}")

    def sanitize_secrets(self, text: str) -> str:
        if not text or not isinstance(text, str):
            return text
        sanitized = text
        for pattern, label in SECRET_PATTERNS:
            sanitized = re.sub(pattern, f"[REDACTED_{label}]", sanitized)
        return sanitized

    def contains_secrets(self, text: str) -> Tuple[bool, list]:
        if not text or not isinstance(text, str):
            return False, []
        found = []
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                found.append(label)
        return len(found) > 0, found

    def is_dangerous_command(self, cmd: str) -> Tuple[bool, str]:
        if not cmd:
            return False, ""
        for pattern in DANGEROUS_COMMANDS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return True, f"Запрещенная деструктивная команда (паттерн: {pattern})"
        return False, ""

    def requires_confirmation(self, action: str, target: str = "") -> bool:
        action_lower = action.lower()
        destructive_keywords = [
            "delete_repo", "delete_agent", "удалить_репозиторий", "удалить_агента",
            "drop_database", "force_push", "delete_service", "удалить_сервис"
        ]
        return any(k in action_lower for k in destructive_keywords)

    def create_confirmation_request(self, user_id: int, action: str, target: str, payload: dict = None) -> str:
        code = f"CONFIRM-{int(time.time()) % 10000:04d}"
        self.pending_confirmations[user_id] = {
            "action": action,
            "target": target,
            "payload": payload or {},
            "code": code,
            "created_at": time.time()
        }
        self.log_audit(action, Permission.DESTRUCTIVE, target, user_id, "PENDING_CONFIRMATION")
        return (
            f"⚠️ **ВНИМАНИЕ: ДЕСТРУКТИВНОЕ ДЕЙСТВИЕ**\n\n"
            f"Действие: `{action}`\n"
            f"Цель: `{target}`\n\n"
            f"Для выполнения отправь точно команду:\n"
            f"👉 `ПОДТВЕРЖДАЮ {code}`\n\n"
            f"Срок действия: 5 минут."
        )

    def check_confirmation(self, user_id: int, message_text: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        req = self.pending_confirmations.get(user_id)
        if not req:
            return False, None

        if time.time() - req["created_at"] > self.timeout:
            del self.pending_confirmations[user_id]
            self.log_audit(req["action"], Permission.DESTRUCTIVE, req["target"], user_id, "CONFIRMATION_EXPIRED")
            return False, None

        text_clean = message_text.strip().upper()
        expected = f"ПОДТВЕРЖДАЮ {req['code']}"

        if text_clean == expected or text_clean == "ПОДТВЕРЖДАЮ":
            del self.pending_confirmations[user_id]
            self.log_audit(req["action"], Permission.DESTRUCTIVE, req["target"], user_id, "CONFIRMED")
            return True, req

        return False, None


security_guard = SecurityGuard()
