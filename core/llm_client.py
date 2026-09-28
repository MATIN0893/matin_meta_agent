import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DEFAULT_GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
]

DEFAULT_OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
]


def _get_env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip()


def call_llm(messages: list, temperature: float = 0.2, max_tokens: int = 4096) -> str:
    """
    Вызывает доступную LLM модель с каскадным переключением (Groq -> OpenRouter).
    Автоматически перебирает рабочие модели при сбоях или исчерпании лимитов.
    """
    groq_key = _get_env("GROQ_API_KEY")
    openrouter_key = _get_env("OPENROUTER_API_KEY")

    configured_groq_model = _get_env("GROQ_MODEL")
    configured_openrouter_model = _get_env("OPENROUTER_MODEL")

    groq_models = []
    if configured_groq_model and configured_groq_model != "openai/gpt-oss-20b":
        groq_models.append(configured_groq_model)
    for m in DEFAULT_GROQ_MODELS:
        if m not in groq_models:
            groq_models.append(m)

    openrouter_models = []
    if configured_openrouter_model:
        openrouter_models.append(configured_openrouter_model)
    for m in DEFAULT_OPENROUTER_MODELS:
        if m not in openrouter_models:
            openrouter_models.append(m)

    errors = []

    # 1. Попытка вызвать Groq (быстрый и стабильный)
    if groq_key:
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
        }
        for model in groq_models:
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                err_msg = f"Groq ({model}) HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(err_msg)
                errors.append(err_msg)
            except Exception as e:
                err_msg = f"Groq ({model}) сбой: {e}"
                logger.warning(err_msg)
                errors.append(err_msg)

    # 2. Попытка вызвать OpenRouter (запасной или основной)
    if openrouter_key:
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://matin-meta-agent.onrender.com",
            "X-Title": "Matin Meta Agent",
        }
        for model in openrouter_models:
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                err_msg = f"OpenRouter ({model}) HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(err_msg)
                errors.append(err_msg)
            except Exception as e:
                err_msg = f"OpenRouter ({model}) сбой: {e}"
                logger.warning(err_msg)
                errors.append(err_msg)

    err_summary = "; ".join(errors) if errors else "API-ключи GROQ_API_KEY и OPENROUTER_API_KEY не заданы"
    raise RuntimeError(f"Все LLM провайдеры недоступны. Причины: {err_summary}")


def check_llm_health() -> bool:
    """Проверка доступности хотя бы одной LLM модели."""
    try:
        res = call_llm([{"role": "user", "content": "ping"}], max_tokens=10)
        return bool(res)
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False


def ask(
    arg1: str = "",
    arg2: str = "",
    *,
    prompt: str = "",
    system_prompt: str = "",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """
    Универсальная точка входа для запросов к LLM.
    Корректно обрабатывает:
    - ask(system_prompt, user_prompt) — позиционные аргументы orchestrator
    - ask(user_prompt) — одиночный запрос
    - ask(prompt=..., system_prompt=...) — именованные аргументы
    """
    if prompt and system_prompt:
        sys_content = system_prompt
        user_content = prompt
    elif prompt and not system_prompt:
        sys_content = arg1 if arg1 != prompt else ""
        user_content = prompt
    elif system_prompt and not prompt:
        sys_content = system_prompt
        user_content = arg1
    elif arg1 and arg2:
        sys_content = arg1
        user_content = arg2
    else:
        sys_content = ""
        user_content = arg1

    messages = []
    if sys_content and sys_content.strip():
        messages.append({"role": "system", "content": sys_content.strip()})
    if user_content and user_content.strip():
        messages.append({"role": "user", "content": user_content.strip()})
    elif not messages:
        messages.append({"role": "user", "content": "ping"})

    return call_llm(messages, temperature=temperature, max_tokens=max_tokens)
