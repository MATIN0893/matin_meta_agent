import time
import requests
from config.settings import GROQ_API_KEY
import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "nvidia/nemotron-3-super-120b-a12b:free"

def ask(arg1: str, arg2: str = "", json_mode: bool = True) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/MATIN0893",
        "X-Title": "Matin Meta Agent"
    }

    if arg2:
        system_content = arg1
        user_content = arg2
    else:
        system_content = ""
        user_content = arg1

    if json_mode and "json" not in (user_content + system_content).lower():
        user_content += "\nReturn ONLY a valid JSON object, no markdown, no explanation."

    messages = []
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.append({"role": "user", "content": user_content})

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 8192
    }

    for attempt in range(1, 6):
        response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
        if response.status_code == 429:
            wait = 30 * attempt
            print(f"[OpenRouter 429] Лимит. Жду {wait} сек... (попытка {attempt}/5)")
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    raise RuntimeError("OpenRouter: превышен лимит после 5 попыток")


