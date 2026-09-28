import json
import os
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MATIN.MEMORY")

MEMORY_FILE_PATH = os.getenv("MATIN_MEMORY_FILE", "data/memory.json")


class EngineeringMemory:
    def __init__(self, file_path: str = MEMORY_FILE_PATH):
        self.file_path = file_path
        self.data: Dict[str, Any] = {
            "projects": {},
            "architecture": {},
            "deployments": [],
            "bugs_and_fixes": [],
            "decisions": [],
            "metadata": {
                "version": "2.0.0",
                "initialized_at": time.time(),
                "last_updated": time.time()
            }
        }
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.data.update(loaded)
            except Exception as e:
                logger.error(f"[MEMORY] Ошибка загрузки памяти: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            self.data["metadata"]["last_updated"] = time.time()
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[MEMORY] Ошибка сохранения памяти: {e}")

    def record_project(self, name: str, repo: str = "", stack: str = "", description: str = "", status: str = "active"):
        self.data["projects"][name] = {
            "name": name,
            "repo": repo,
            "stack": stack,
            "description": description,
            "status": status,
            "updated_at": time.time()
        }
        self.save()

    def get_project(self, name: str) -> Optional[Dict[str, Any]]:
        return self.data["projects"].get(name)

    def list_projects(self) -> List[Dict[str, Any]]:
        return list(self.data["projects"].values())

    def record_deployment(self, service_name: str, commit_sha: str = "", status: str = "success", notes: str = ""):
        entry = {
            "timestamp": time.time(),
            "service_name": service_name,
            "commit_sha": commit_sha,
            "status": status,
            "notes": notes
        }
        self.data["deployments"].append(entry)
        if len(self.data["deployments"]) > 200:
            self.data["deployments"] = self.data["deployments"][-200:]
        self.save()

    def record_bug_fix(self, project: str, error_text: str, root_cause: str, fix_description: str, confidence: float = 1.0):
        entry = {
            "timestamp": time.time(),
            "project": project,
            "error_text": error_text[:500],
            "root_cause": root_cause,
            "fix_description": fix_description,
            "confidence": confidence
        }
        self.data["bugs_and_fixes"].append(entry)
        if len(self.data["bugs_and_fixes"]) > 200:
            self.data["bugs_and_fixes"] = self.data["bugs_and_fixes"][-200:]
        self.save()

    def record_decision(self, title: str, context: str, decision: str, impact: str = ""):
        entry = {
            "timestamp": time.time(),
            "title": title,
            "context": context,
            "decision": decision,
            "impact": impact
        }
        self.data["decisions"].append(entry)
        self.save()

    def search_memory(self, query: str) -> Dict[str, Any]:
        q = query.lower()
        return {
            "projects": [p for p in self.list_projects() if q in p["name"].lower() or q in p.get("description", "").lower()],
            "decisions": [d for d in self.data["decisions"] if q in d["title"].lower() or q in d["decision"].lower()],
            "bugs": [b for b in self.data["bugs_and_fixes"] if q in b["project"].lower() or q in b["error_text"].lower()]
        }


memory = EngineeringMemory()
