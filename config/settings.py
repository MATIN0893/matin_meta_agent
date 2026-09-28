import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")
TG_BOT_TOKEN = TELEGRAM_BOT_TOKEN
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID") or os.getenv("ADMIN_ID")
ADMIN_ID = TELEGRAM_ADMIN_ID

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "MATIN0893")

RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")

def is_user_allowed(user_id: int) -> bool:
    raw = os.getenv("ALLOWED_USERS", "") or ALLOWED_USERS
    if not raw:
        return True
    if isinstance(raw, (list, tuple, set)):
        return int(user_id) in [int(x) for x in raw if str(x).strip().isdigit()]
    allowed_ids = [int(u.strip()) for u in str(raw).split(",") if u.strip().isdigit()]
    if not allowed_ids:
        return True
    return int(user_id) in allowed_ids
