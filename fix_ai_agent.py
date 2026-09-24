import json
import logging
import httpx
from .config import Settings
from .models import AgentCommand

logger = logging.getLogger(__name__)

async def interpret(text: str, settings: Settings) -> AgentCommand:
    if not settings.openrouter_api_key:
        return AgentCommand(action="status", instruction="OPENROUTER_API_KEY is not configured")
    try:
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/MATIN0893",
            "X-Title": "matin-agent"
        }
        payload = {
            "model": "nvidia/nemotron-3-super-120b-a12b:free",
            "messages": [
                {"role": "system", "content": "You are MATIN Agent. Return JSON only with keys: action, repository, path, instruction. Allowed actions: inspect, edit, test, run, status."},
                {"role": "user", "content": text}
            ],
            "temperature": 0.2,
            "max_tokens": 1024
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            raw = data["choices"][0]["message"]["content"]
            parsed = json.loads(raw)
            return AgentCommand(**parsed)
    except Exception as exc:
        logger.exception("interpret failed")
        return AgentCommand(action="status", instruction=f"Error: {exc}")
