import os
from dotenv import load_dotenv
load_dotenv()

AGENT_NAME = 'Matin Monitor'
AGENT_ID = 'matin-monitor'
PORT = int(os.getenv('PORT', 10000))
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN', '')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
