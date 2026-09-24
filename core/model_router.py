from dotenv import load_dotenv
load_dotenv()
import os
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    "nex-agi/nex-n2.5-mini:free",
    "nex-agi/nex-n2.5-pro:free",
    "inclusionai/ling-3.0-flash-sante:free"
]

def query_model(prompt: str, system_prompt: str = "") -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY отсутствует в .env")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/MATIN0893",
        "X-Title": "Matin Meta Agent"
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    for model in DEFAULT_MODELS:
        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.2
            }
            res = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            last_error = f"{model} status {res.status_code}: {res.text}"
        except Exception as e:
            last_error = str(e)
            continue

    raise RuntimeError(f"Все модели недоступны. Последняя: {last_error}")

