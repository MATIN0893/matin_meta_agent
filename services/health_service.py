import json
import time
import os
import logging
import requests
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

_github_status_cache = {"status": "unknown", "checked_at": 0.0}


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


def check_github_status() -> dict:
    """
    Проверяет реальный статус GitHub API.
    Не маскирует 404 или ошибки как OK.
    """
    global _github_status_cache
    now = time.time()
    if now - _github_status_cache["checked_at"] < 60:
        return _github_status_cache

    if not GITHUB_TOKEN:
        res = {"status": "no_token", "username": GITHUB_USERNAME or "not_configured", "checked_at": now}
        _github_status_cache = res
        return res

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "Matin-Meta-Agent"
    }

    try:
        resp = requests.get("https://api.github.com/user", headers=headers, timeout=5)
        if resp.status_code == 200:
            user_data = resp.json()
            res = {
                "status": "ok",
                "username": user_data.get("login") or GITHUB_USERNAME,
                "checked_at": now
            }
        elif resp.status_code == 404:
            res = {
                "status": "http_404",
                "error": "GitHub API /user returned 404 Not Found",
                "username": GITHUB_USERNAME or "not_configured",
                "checked_at": now
            }
        else:
            res = {
                "status": f"http_{resp.status_code}",
                "error": f"GitHub API error {resp.status_code}",
                "username": GITHUB_USERNAME or "not_configured",
                "checked_at": now
            }
    except Exception as e:
        res = {
            "status": "network_error",
            "error": str(e),
            "username": GITHUB_USERNAME or "not_configured",
            "checked_at": now
        }

    _github_status_cache = res
    return res


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

    gh_info = check_github_status()

    overall_status = "ok"
    if patrol_status == "degraded" or gh_info.get("status") in ("http_404", "network_error"):
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
            "github": gh_info,
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
