import json
import os
import time
import logging
from enum import Enum
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MATIN.AGENT_REGISTRY")

REGISTRY_FILE_PATH = os.getenv("MATIN_AGENT_REGISTRY_FILE", "agents/registry.json")


class AgentLifecycle(str, Enum):
    CREATED = "created"
    CONFIGURED = "configured"
    TESTING = "testing"
    READY = "ready"
    DEPLOYED = "deployed"
    ACTIVE = "active"
    ERROR = "error"
    RECOVERING = "recovering"
    STOPPED = "stopped"


class AgentRecord:
    def __init__(
        self,
        agent_id: str,
        name: str,
        status: AgentLifecycle = AgentLifecycle.CREATED,
        version: str = "1.0.0",
        description: str = "",
        permissions: List[str] = None,
        github_repo: str = "",
        render_service_id: str = "",
        health_url: str = "",
        created_at: float = None,
        updated_at: float = None,
        last_health_check: Dict[str, Any] = None,
        config: Dict[str, Any] = None
    ):
        self.agent_id = agent_id
        self.name = name
        self.status = status
        self.version = version
        self.description = description
        self.permissions = permissions or []
        self.github_repo = github_repo
        self.render_service_id = render_service_id
        self.health_url = health_url
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.last_health_check = last_health_check or {}
        self.config = config or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, AgentLifecycle) else str(self.status),
            "version": self.version,
            "description": self.description,
            "permissions": self.permissions,
            "github_repo": self.github_repo,
            "render_service_id": self.render_service_id,
            "health_url": self.health_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_health_check": self.last_health_check,
            "config": self.config
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentRecord":
        status_val = data.get("status", AgentLifecycle.CREATED.value)
        try:
            status = AgentLifecycle(status_val)
        except ValueError:
            status = AgentLifecycle.CREATED

        return cls(
            agent_id=data.get("agent_id", ""),
            name=data.get("name", ""),
            status=status,
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            permissions=data.get("permissions", []),
            github_repo=data.get("github_repo", ""),
            render_service_id=data.get("render_service_id", ""),
            health_url=data.get("health_url", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            last_health_check=data.get("last_health_check", {}),
            config=data.get("config", {})
        )


class AgentRegistry:
    def __init__(self, file_path: str = REGISTRY_FILE_PATH):
        self.file_path = file_path
        self.agents: Dict[str, AgentRecord] = {}
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for aid, arec in data.items():
                            self.agents[aid] = AgentRecord.from_dict(arec)
            except Exception as e:
                logger.error(f"[AGENT_REGISTRY] Ошибка загрузки реестра: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            serialized = {aid: a.to_dict() for aid, a in self.agents.items()}
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[AGENT_REGISTRY] Ошибка сохранения реестра: {e}")

    def register_agent(
        self,
        agent_id: str,
        name: str,
        description: str = "",
        permissions: List[str] = None,
        github_repo: str = "",
        render_service_id: str = "",
        health_url: str = "",
        config: Dict[str, Any] = None
    ) -> AgentRecord:
        agent = AgentRecord(
            agent_id=agent_id,
            name=name,
            status=AgentLifecycle.CONFIGURED,
            description=description,
            permissions=permissions or [],
            github_repo=github_repo,
            render_service_id=render_service_id,
            health_url=health_url,
            config=config or {}
        )
        self.agents[agent_id] = agent
        self.save()
        return agent

    def get_agent(self, agent_id: str) -> Optional[AgentRecord]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[AgentRecord]:
        return list(self.agents.values())

    def update_status(self, agent_id: str, status: AgentLifecycle, health_data: dict = None) -> bool:
        agent = self.agents.get(agent_id)
        if not agent:
            return False
        agent.status = status
        agent.updated_at = time.time()
        if health_data:
            agent.last_health_check = health_data
        self.save()
        return True

    def delete_agent(self, agent_id: str) -> bool:
        if agent_id in self.agents:
            del self.agents[agent_id]
            self.save()
            return True
        return False


agent_registry = AgentRegistry()
