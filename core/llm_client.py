import time
import requests
from config.settings import GEMINI_API_KEY

def ask(system: str, user: str, max_tokens: int = 8192, max_retries: int = 4) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
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

    last_response = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            last_response = resp

            # Если Google временно перегружен (503, 429, 500, 502, 504) — ждём и повторяем
            if resp.status_code in (429, 500, 502, 503, 504):
                sleep_sec = attempt * 2
                time.sleep(sleep_sec)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except (requests.ConnectionError, requests.Timeout):
            if attempt < max_retries:
                time.sleep(attempt * 2)
                continue
            raise

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError("Не удалось получить ответ от Gemini после нескольких попыток.")