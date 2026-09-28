import logging
from core.llm_client import ask, call_llm

logger = logging.getLogger(__name__)


def query_model(prompt: str, system_prompt: str = "") -> str:
    """Маршрутизация запроса через централизованный LLM-клиент (с поддержкой Groq и OpenRouter)."""
    return ask(prompt=prompt, system_prompt=system_prompt)
