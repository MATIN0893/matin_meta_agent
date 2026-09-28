import json
import os
import time
import uuid
import logging
from enum import Enum
from typing import Dict, Any, List, Optional

logger = logging.getLogger("MATIN.TASK_ENGINE")

TASKS_FILE_PATH = os.getenv("MATIN_TASKS_FILE", "data/tasks.json")


class TaskState(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    TESTING = "testing"
    VALIDATING = "validating"
    DEPLOYING = "deploying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"


class Task:
    def __init__(
        self,
        task_id: str,
        user_id: int,
        command: str,
        state: TaskState = TaskState.PENDING,
        plan: Any = None,
        actions: List[Dict[str, Any]] = None,
        result: str = "",
        error: str = "",
        retries: int = 0,
        created_at: float = None,
        updated_at: float = None
    ):
        self.task_id = task_id
        self.user_id = user_id
        self.command = command
        self.state = state
        self.plan = plan or {}
        self.actions = actions or []
        self.result = result
        self.error = error
        self.retries = retries
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "command": self.command,
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "plan": self.plan,
            "actions": self.actions,
            "result": self.result,
            "error": self.error,
            "retries": self.retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        state_val = data.get("state", TaskState.PENDING.value)
        try:
            state = TaskState(state_val)
        except ValueError:
            state = TaskState.PENDING

        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())[:8]),
            user_id=data.get("user_id", 0),
            command=data.get("command", ""),
            state=state,
            plan=data.get("plan"),
            actions=data.get("actions"),
            result=data.get("result", ""),
            error=data.get("error", ""),
            retries=data.get("retries", 0),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at")
        )

    def log_action(self, action_name: str, status: str = "ok", details: str = ""):
        self.actions.append({
            "timestamp": time.time(),
            "action": action_name,
            "status": status,
            "details": details
        })
        self.updated_at = time.time()


class TaskEngine:
    def __init__(self, file_path: str = TASKS_FILE_PATH):
        self.file_path = file_path
        self.tasks: Dict[str, Task] = {}
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for tid, tdata in data.items():
                            self.tasks[tid] = Task.from_dict(tdata)
            except Exception as e:
                logger.error(f"[TASK_ENGINE] Ошибка загрузки задач: {e}")

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            serialized = {}
            sorted_tasks = sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)[:100]
            for t in sorted_tasks:
                serialized[t.task_id] = t.to_dict()

            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[TASK_ENGINE] Ошибка сохранения задач: {e}")

    def create_task(self, user_id: int, command: str) -> Task:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(task_id=task_id, user_id=user_id, command=command)
        self.tasks[task_id] = task
        self.save()
        return task

    def update_state(self, task_id: str, state: TaskState, result: str = "", error: str = ""):
        task = self.tasks.get(task_id)
        if task:
            task.state = state
            if result:
                task.result = result
            if error:
                task.error = error
            task.updated_at = time.time()
            self.save()

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def list_active_tasks(self) -> List[Task]:
        active_states = {
            TaskState.PENDING, TaskState.PLANNING,
            TaskState.EXECUTING, TaskState.TESTING,
            TaskState.VALIDATING, TaskState.DEPLOYING
        }
        return [t for t in self.tasks.values() if t.state in active_states]

    def recover_interrupted_tasks(self) -> List[Task]:
        active = self.list_active_tasks()
        recovered = []
        for t in active:
            t.state = TaskState.RECOVERED
            t.log_action("crash_recovery", status="recovered", details="Задача была прервана перезапуском облачного инстанса")
            t.updated_at = time.time()
            recovered.append(t)
        if recovered:
            self.save()
        return recovered


task_engine = TaskEngine()
