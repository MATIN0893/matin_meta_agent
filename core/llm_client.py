import json
import re
import time
import requests
from config.settings import GEMINI_API_KEY

def extract_retry_delay(resp_text: str, default_delay: int) -> int:
    try:
        data = json.loads(resp_text)
        details = data.get("error", {}).get("details", [])
        for item in details:
            delay_str = item.get("retryDelay")
            if delay_str:
                match = re.search(r"(\d+)", delay_str)
                if match:
                    return int(match.group(1)) + 5
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

    max_attempts = 6
    last_response = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            last_response = resp

            # 429: Лимит запросов/токенов в минуту
            if resp.status_code == 429:
                # На бесплатном тарифе окно сброса 60 секунд. Выжидаем 65 секунд с гарантией
                wait_sec = extract_retry_delay(resp.text, 65)
                print(f"[Gemini 429] Исчерпан минутный лимит. Ожидание сброса квоты {wait_sec} сек... (Попытка {attempt}/{max_attempts})")
                time.sleep(wait_sec)
                continue

            # 503 / 500 / 502 / 504: Временная перегрузка узла Google
            if resp.status_code in (500, 502, 503, 504):
                wait_sec = 10 * attempt
                print(f"[Gemini {resp.status_code}] Сервер Google перегружен. Пауза {wait_sec} сек... (Попытка {attempt}/{max_attempts})")
                time.sleep(wait_sec)
                continue

            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except (requests.ConnectionError, requests.Timeout) as net_err:
            wait_sec = 10 * attempt
            print(f"[Gemini Network] Ошибка сети: {net_err}. Пауза {wait_sec} сек...")
            if attempt < max_attempts:
                time.sleep(wait_sec)
                continue
            raise

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError("Превышен лимит запросов к Gemini после всех попыток ожидания.")