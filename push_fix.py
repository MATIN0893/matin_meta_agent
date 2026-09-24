from dotenv import load_dotenv; load_dotenv()
from services.github_service import get_repo_files, push_project
f = get_repo_files("matin-agent")
f["app/ai_agent.py"] = open("fix_ai_agent.py", encoding="utf-8").read()
push_project("matin-agent", f)
print("ГОТОВО")
