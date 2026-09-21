import requests
from config.settings import GEMINI_API_KEY

def ask(system: str, user: str, max_tokens: int = 4096) -> str:
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}
        ],
        "generationConfig": {"maxOutputTokens": max_tokens}
    }
    
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
