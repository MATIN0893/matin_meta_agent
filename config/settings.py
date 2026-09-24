import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")
TG_BOT_TOKEN = TELEGRAM_BOT_TOKEN
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID") or os.getenv("ADMIN_ID")
ADMIN_ID = TELEGRAM_ADMIN_ID

# LLM (Groq)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")

# Render
RENDER_DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")
RENDER_API_KEY = os.getenv("RENDER_API_KEY")