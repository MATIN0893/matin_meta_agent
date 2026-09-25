import os
import logging
import requests

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")


def call_llm(messages: list, temperature: float = 0.7, max_tokens: int = 1500) -> str:
    if OPENROUTER_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://matin-meta-agent.onrender.com",
                "X-Title": "Matin Meta Agent",
            }
            payload = {
                "model": DEFAULT_OPENROUTER_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.warning(f"OpenRouter {resp.status_code}, переход на Groq...")
        except Exception as e:
            logger.error(f"OpenRouter сбой: {e}, переход на Groq...")

    if GROQ_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": DEFAULT_GROQ_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.error(f"Groq {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Groq сбой: {e}")

    raise RuntimeError("Все LLM провайдеры недоступны.")


def check_llm_health() -> bool:
    try:
        call_llm([{"role": "user", "content": "ping"}], max_tokens=5)
        return True
    except Exception:
        return False

def ask(prompt: str, system_prompt: str = "") -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return call_llm(messages)
