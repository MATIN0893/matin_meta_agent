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
    TIMEOUT = "timeout"
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
        updated_at: float = None,
        target_repo: str = "",
        target_branch: str = "main",
        original_intent: str = "",
        task_type: str = "",
        current_stage: str = "",
        audit_trail: List[Dict[str, Any]] = None,
    ):
        self.task_id = task_id
        self.user_id = user_id
        self.command = command
        self.state = state
        self.plan = plan or {}
        self.actions = actions or []
        self.audit_trail = audit_trail or (actions or [])
        self.result = result
        self.error = error
        self.retries = retries
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.target_repo = target_repo
        self.target_branch = target_branch
        self.original_intent = original_intent or command
        self.task_type = task_type
        self.current_stage = current_stage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "command": self.command,
            "state": self.state.value if isinstance(self.state, TaskState) else str(self.state),
            "plan": self.plan,
            "actions": self.actions,
            "audit_trail": self.audit_trail,
            "result": self.result,
            "error": self.error,
            "retries": self.retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "target_repo": self.target_repo,
            "target_branch": self.target_branch,
            "original_intent": self.original_intent,
            "task_type": self.task_type,
            "current_stage": self.current_stage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        state_val = data.get("state", TaskState.PENDING.value)
        try:
            state = TaskState(state_val)
        except ValueError:
            state = TaskState.PENDING

        actions = data.get("actions") or data.get("audit_trail") or []
        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())[:8]),
            user_id=data.get("user_id", 0),
            command=data.get("command", ""),
            state=state,
            plan=data.get("plan"),
            actions=actions,
            audit_trail=actions,
            result=data.get("result", ""),
            error=data.get("error", ""),
            retries=data.get("retries", 0),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            target_repo=data.get("target_repo", ""),
            target_branch=data.get("target_branch", "main"),
            original_intent=data.get("original_intent", ""),
            task_type=data.get("task_type", ""),
            current_stage=data.get("current_stage", ""),
        )

    def log_action(self, action_name: str, status: str = "ok", details: str = ""):
        entry = {
            "timestamp": time.time(),
            "action": action_name,
            "status": status,
            "details": details,
        }
        self.actions.append(entry)
        self.audit_trail.append(entry)
        self.updated_at = time.time()


class TaskEngine:
    def __init__(self, file_path: str = TASKS_FILE_PATH):
        self.file_path = file_path
        self.tasks: Dict[str, Task] = {}
        self.active_task_id: Optional[str] = None
        self.active_async_task: Any = None
        self.last_target_repo: str = ""
        self.last_target_branch: str = "main"
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

    # --- TASK LOCK & CONCURRENCY ---
    def is_locked(self) -> bool:
        if not self.active_task_id:
            return False
        task = self.tasks.get(self.active_task_id)
        if not task:
            self.active_task_id = None
            self.active_async_task = None
            return False
        active_states = {
            TaskState.PENDING, TaskState.PLANNING,
            TaskState.EXECUTING, TaskState.TESTING,
            TaskState.VALIDATING, TaskState.DEPLOYING
        }
        if task.state in active_states:
            return True
        self.active_task_id = None
        self.active_async_task = None
        return False

    def get_active_task(self) -> Optional[Task]:
        if self.is_locked():
            return self.tasks.get(self.active_task_id)
        return None

    def acquire_lock(self, task: Task, async_task: Any = None) -> bool:
        if self.is_locked() and self.active_task_id != task.task_id:
            return False
        self.active_task_id = task.task_id
        self.active_async_task = async_task
        if task.target_repo:
            self.last_target_repo = task.target_repo
        if task.target_branch:
            self.last_target_branch = task.target_branch
        return True

    def release_lock(self, task_id: str = None):
        if task_id is None or self.active_task_id == task_id:
            self.active_task_id = None
            self.active_async_task = None

    def cancel_current_task(self, reason: str = "Отменено пользователем") -> Optional[Task]:
        task = self.get_active_task()
        if not task:
            return None

        task.state = TaskState.CANCELLED
        task.error = reason
        task.log_action("cancelled", status="cancelled", details=reason)
        task.updated_at = time.time()
        self.save()

        if self.active_async_task:
            try:
                if hasattr(self.active_async_task, "cancel") and callable(self.active_async_task.cancel):
                    self.active_async_task.cancel()
            except Exception as e:
                logger.warning(f"Ошибка отмены async task: {e}")

        self.release_lock(task.task_id)
        return task

    # --- CONTEXT PERSISTENCE ---
    def get_last_target_repo(self) -> str:
        if self.last_target_repo:
            return self.last_target_repo
        for t in sorted(self.tasks.values(), key=lambda x: x.updated_at, reverse=True):
            if t.target_repo:
                return t.target_repo
        return ""

    def set_last_target_repo(self, repo: str, branch: str = "main"):
        if repo and repo.strip():
            self.last_target_repo = repo.strip()
        if branch and branch.strip():
            self.last_target_branch = branch.strip()

    # --- TASK LIFECYCLE ---
    def create_task(self, user_id: int, command: str) -> Task:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(task_id=task_id, user_id=user_id, command=command)
        self.tasks[task_id] = task
        self.save()
        return task

    def update_state(
        self,
        task_id: str,
        state: TaskState,
        result: str = "",
        error: str = "",
        current_stage: str = ""
    ):
        task = self.tasks.get(task_id)
        if task:
            task.state = state
            if result:
                task.result = result
            if error:
                task.error = error
            if current_stage:
                task.current_stage = current_stage
            task.updated_at = time.time()

            terminal_states = {
                TaskState.COMPLETED, TaskState.FAILED,
                TaskState.CANCELLED, TaskState.TIMEOUT,
                TaskState.RECOVERED
            }
            if state in terminal_states:
                self.release_lock(task_id)
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
        self.release_lock()
        if recovered:
            self.save()
        return recovered


task_engine = TaskEngine()
