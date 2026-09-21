import json
import requests
from config.settings import GEMINI_API_KEY

_CACHED_MODEL_NAME = None

def get_working_model() -> str:
    """Запрашивает у Google список всех доступных для ключа моделей и выбирает поддерживающую generateContent."""
    global _CACHED_MODEL_NAME
    if _CACHED_MODEL_NAME:
        return _CACHED_MODEL_NAME

    for api_version in ["v1beta", "v1"]:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models?key={GEMINI_API_KEY}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                # Ищем модель, которая поддерживает генерацию текста и содержит flash или pro
                for m in models:
                    methods = m.get("supportedGenerationMethods", [])
                    name = m.get("name", "")  # формат: models/gemini-1.5-flash
                    if "generateContent" in methods:
                        _CACHED_MODEL_NAME = (api_version, name)
                        return _CACHED_MODEL_NAME
        except Exception:
            pass

    # Если список получить не удалось, используем дефолт
    return ("v1beta", "models/gemini-1.5-flash")

def ask(system: str, user: str, max_tokens: int = 8192) -> str:
    api_version, model_name = get_working_model()

    # Очищаем префикс models/ если передается в URL
    model_slug = model_name.replace("models/", "")
    url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model_slug}:generateContent?key={GEMINI_API_KEY}"

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

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    
    # Если строгий responseMimeType не поддерживается моделью, пробуем без него
    if resp.status_code == 400 and "responseMimeType" in resp.text:
        payload["generationConfig"].pop("responseMimeType", None)
        resp = requests.post(url, headers=headers, json=payload, timeout=120)

    if resp.status_code != 200:
        raise RuntimeError(f"Google API Error ({resp.status_code}): {resp.text}")

    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]