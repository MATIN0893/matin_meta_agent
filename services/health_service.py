import json
import time
import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from config.settings import GITHUB_TOKEN, GITHUB_USERNAME, RENDER_API_KEY
from core.task_engine import task_engine
from core.memory import memory
from engine.agent_registry import agent_registry

logger = logging.getLogger("MATIN.HEALTH")
START_TIME = time.time()

PATROL_STATS = {
    "is_alive": False,
    "last_run": 0.0,
    "total_runs": 0,
    "consecutive_errors": 0,
    "last_error": ""
}


def update_patrol_heartbeat(success: bool = True, error_msg: str = ""):
    PATROL_STATS["is_alive"] = True
    PATROL_STATS["last_run"] = time.time()
    PATROL_STATS["total_runs"] += 1
    if success:
        PATROL_STATS["consecutive_errors"] = 0
        PATROL_STATS["last_error"] = ""
    else:
        PATROL_STATS["consecutive_errors"] += 1
        PATROL_STATS["last_error"] = error_msg


def get_full_health_report() -> dict:
    now = time.time()
    uptime = round(now - START_TIME, 1)

    patrol_status = "ok"
    if not PATROL_STATS["is_alive"] and (now - START_TIME > 60):
        patrol_status = "inactive"
    elif PATROL_STATS["consecutive_errors"] > 2:
        patrol_status = "degraded"

    active_tasks = task_engine.list_active_tasks()
    agents = agent_registry.list_agents()

    overall_status = "ok"
    if patrol_status == "degraded":
        overall_status = "degraded"

    return {
        "status": overall_status,
        "os_version": "MATIN META OS 2.0",
        "cloud_24_7": True,
        "pc_dependent": False,
        "uptime_seconds": uptime,
        "timestamp": now,
        "services": {
            "telegram": {
                "status": "ok",
                "mode": "polling"
            },
            "patrol": {
                "status": patrol_status,
                "total_runs": PATROL_STATS["total_runs"],
                "last_run_seconds_ago": round(now - PATROL_STATS["last_run"], 1) if PATROL_STATS["last_run"] > 0 else None,
                "consecutive_errors": PATROL_STATS["consecutive_errors"]
            },
            "github": {
                "status": "ok" if GITHUB_TOKEN else "no_token",
                "username": GITHUB_USERNAME or "not_configured"
            },
            "render": {
                "status": "ok" if RENDER_API_KEY else "no_key"
            }
        },
        "metrics": {
            "registered_agents_count": len(agents),
            "active_tasks_count": len(active_tasks),
            "known_projects_count": len(memory.list_projects())
        }
    }


class DynamicHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/ping", "/pong"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"PONG")
            return

        if self.path in ("/health", "/status", "/json"):
            report = get_full_health_report()
            body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200 if report["status"] == "ok" else 207)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"MATIN META ENGINEERING OS 2.0 (ONLINE 24/7)")

    def log_message(self, format, *args):
        pass


def run_health_server(port: int = None):
    p = port or int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", p), DynamicHealthHandler)
    logger.info(f"[HEALTH SERVER] Запущен на порту {p}")
    server.serve_forever()
