import json
import re
import time
import requests
from config.settings import GEMINI_API_KEY

def extract_retry_delay(resp_text: str, default_delay: int) -> int:
    """Извлекает точное время ожидания, рекомендованное сервером Google при 429."""
    try:
        data = json.loads(resp_text)
        details = data.get("error", {}).get("details", [])
        for item in details:
            delay_str = item.get("retryDelay")
            if delay_str:
                match = re.search(r"(\d+)", delay_str)
                if match:
                    return int(match.group(1)) + 2
    except Exception:
        pass
    return default_delay

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

    # Интервалы повторов с запасом для сброса минутных лимитов
    delays = [15, 25, 40, 60]
    last_response = None

    for attempt, default_delay in enumerate(delays, start=1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            last_response = resp

            # 429: Превышение квоты запросов/токенов
            if resp.status_code == 429:
                wait_sec = extract_retry_delay(resp.text, default_delay)
                print(f"[Gemini 429 Rate Limit] Квота исчерпана. Ожидаем сброса окна {wait_sec} сек... (Попытка {attempt}/{len(delays)})")
                time.sleep(wait_sec)
                continue

            # 503 / 500 / 502 / 504: Временная недоступность сервиса
            if resp.status_code in (500, 502, 503, 504):
                print(f"[Gemini {resp.status_code}] Сервер перегружен. Пауза {default_delay} сек... (Попытка {attempt}/{len(delays)})")
                time.sleep(default_delay)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except (requests.ConnectionError, requests.Timeout) as net_err:
            print(f"[Gemini Network] Ошибка сети: {net_err}. Пауза {default_delay} сек...")
            if attempt < len(delays):
                time.sleep(default_delay)
                continue
            raise

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError("Превышен лимит запросов к Gemini после всех попыток ожидания.")