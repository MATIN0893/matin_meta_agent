import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")
TG_BOT_TOKEN = TELEGRAM_BOT_TOKEN
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# Список ID пользователей в Telegram (через запятую в env или пустой список)
raw_allowed = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = [int(uid.strip()) for uid in raw_allowed.split(",") if uid.strip().isdigit()]

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задана в окружении или файле .env")