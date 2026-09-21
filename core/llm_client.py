import json
import requests
from config.settings import GEMINI_API_KEY

CANDIDATE_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-1.5-pro"
]

def ask(system: str, user: str, max_tokens: int = 8192) -> str:
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}
        ],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    last_error = ""

    # Перебираем рабочие модели
    for model in CANDIDATE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            last_error = f"Model {model} returned HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            last_error = str(e)

    # Если ни одна модель не ответила через v1beta, пробуем v1
    for model in ["gemini-1.5-flash", "gemini-pro"]:
        url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            last_error = f"v1 Model {model} returned HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(f"Не удалось подключиться к моделям Gemini. Детали: {last_error}")