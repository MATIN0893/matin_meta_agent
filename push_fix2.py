from dotenv import load_dotenv; load_dotenv()
from services.github_service import get_repo_files, push_project
f = get_repo_files("matin-agent")

config = f.get("app/config.py", "")
if "openrouter_api_key" not in config:
    config = config.replace(
        'groq_api_key: str = ""',
        'groq_api_key: str = ""\n    openrouter_api_key: str = ""'
    )
    f["app/config.py"] = config
    push_project("matin-agent", f)
    print("ГОТОВО")
else:
    print("УЖЕ ЕСТЬ")
