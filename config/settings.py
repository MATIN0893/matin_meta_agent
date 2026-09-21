import os
from dotenv import load_dotenv

load_dotenv()

# Поддерживаем все три варианта названия токена
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TG_BOT_TOKEN")
    or os.getenv("BOT_TOKEN", "")
)
TG_BOT_TOKEN = BOT_TOKEN

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "MATIN0893")
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK", "")
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")

# Разрешенные ID пользователей через запятую
raw_users = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(u.strip()) for u in raw_users.split(",") if u.strip().isdigit()]