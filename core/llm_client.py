import time
import requests
from config.settings import GEMINI_API_KEY

RETRY_DELAYS = [3, 6, 12, 20, 25]

def ask(system: str, user: str, max_tokens: int = 8192) -> str:
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

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            last_response = resp

            # 503 (Overloaded), 429 (Rate Limit), 500/502/504
            if resp.status_code in (429, 500, 502, 503, 504):
                print(f"[Gemini API] Сервер перегружен ({resp.status_code}). Повтор {attempt}/{len(RETRY_DELAYS)} через {delay} сек...")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except (requests.ConnectionError, requests.Timeout) as net_err:
            print(f"[Gemini API] Ошибка сети ({net_err}). Повтор {attempt}/{len(RETRY_DELAYS)} через {delay} сек...")
            if attempt < len(RETRY_DELAYS):
                time.sleep(delay)
                continue
            raise

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError("Не удалось получить ответ от Gemini после всех попыток ожидания очереди.")