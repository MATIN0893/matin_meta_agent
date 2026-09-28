import os
import re
import difflib
import base64
import requests
from config.settings import GITHUB_TOKEN, GITHUB_USERNAME

try:
    from github import Github, GithubException
except ImportError:
    Github = None
    class GithubException(Exception):
        pass

IGNORE_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "__pycache__",
    ".pytest_cache", ".idea", ".vscode", "node_modules", "dist", "build", "logs"
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".svg", ".webp", ".zip", ".tar", ".gz", ".exe",
    ".dll", ".so", ".dylib", ".pdf", ".woff", ".woff2", ".ttf",
    ".lock", ".log", ".sqlite", ".sqlite3", ".db", ".csv", ".session",
    ".session-journal"
}

MAX_FILE_SIZE_BYTES = 50 * 1024


class RepoInfo(dict):
    """Словарь с доступом к атрибутам представляющий репозиторий GitHub."""
    def __init__(self, name: str, full_name: str, default_branch: str, html_url: str, private: bool = False, description: str = "", **kwargs):
        super().__init__(
            name=name,
            full_name=full_name,
            default_branch=default_branch,
            html_url=html_url,
            private=private,
            description=description,
            **kwargs
        )
        self.name = name
        self.full_name = full_name
        self.default_branch = default_branch
        self.html_url = html_url
        self.private = private
        self.description = description

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)

    def __str__(self):
        return self.name


def normalize_repo_name(name: str) -> str:
    """Нормализует имя репозитория для сравнения: нижний регистр, без дефисов, подчеркиваний и спецсимволов."""
    if not name:
        return ""
    if "/" in name:
        name = name.split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9]", "", name).lower()


def get_github_client():
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN не задан в переменных окружения.")
    if Github is None:
        raise ImportError("Пакет PyGithub не установлен.")
    return Github(GITHUB_TOKEN)


def _get_auth_headers(accept: str = "application/vnd.github.v3+json") -> dict:
    headers = {
        "Accept": accept,
        "User-Agent": "Matin-Meta-Agent"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def check_repo_exists(repo_name: str) -> dict:
    """
    Проверяет реальный GitHub API endpoint: https://api.github.com/repos/{owner}/{repo}.
    Возвращает точный статус без маскировки HTTP 404 как OK.
    """
    if not repo_name:
        return {"exists": False, "status_code": 400, "message": "Имя репозитория пустое"}

    owner = GITHUB_USERNAME or "MATIN0893"
    target = repo_name.strip()
    full_name = target if "/" in target else f"{owner}/{target}"
    url = f"https://api.github.com/repos/{full_name}"

    try:
        resp = requests.get(url, headers=_get_auth_headers(), timeout=10)
        if resp.status_code == 200:
            return {"exists": True, "status_code": 200, "full_name": full_name, "message": "OK"}
        elif resp.status_code == 404:
            return {
                "exists": False,
                "status_code": 404,
                "full_name": full_name,
                "message": f"Репозиторий '{full_name}' не найден на GitHub (HTTP 404 Not Found)"
            }
        else:
            return {
                "exists": False,
                "status_code": resp.status_code,
                "full_name": full_name,
                "message": f"GitHub API вернул статус {resp.status_code}"
            }
    except Exception as e:
        return {"exists": False, "status_code": 0, "full_name": full_name, "message": str(e)}


def get_repo_metadata(repo_name: str) -> dict:
    """
    READ-ONLY: Получает полные метаданные репозитория:
    имя, полное имя, ветка по умолчанию, приватность, описание, html_url,
    с четкой диагностикой статуса и эндпоинта без вывода секретов.
    """
    if not repo_name:
        return {
            "success": False,
            "status_code": 400,
            "error": "Имя репозитория пустое",
            "repository": "",
            "endpoint": ""
        }

    owner = GITHUB_USERNAME or "MATIN0893"
    target = repo_name.strip()
    full_name = target if "/" in target else f"{owner}/{target}"
    endpoint = f"https://api.github.com/repos/{full_name}"

    try:
        resp = requests.get(endpoint, headers=_get_auth_headers(), timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "name": data.get("name", target.split("/")[-1]),
                "full_name": data.get("full_name", full_name),
                "default_branch": data.get("default_branch", "main"),
                "private": data.get("private", False),
                "description": data.get("description") or "",
                "html_url": data.get("html_url") or f"https://github.com/{full_name}",
                "status_code": 200,
                "endpoint": endpoint,
                "repository": full_name
            }
        elif resp.status_code == 404:
            return {
                "success": False,
                "status_code": 404,
                "error": f"Репозиторий '{full_name}' не найден (HTTP 404 Not Found).",
                "endpoint": endpoint,
                "repository": full_name
            }
        elif resp.status_code in (401, 403):
            err_reason = "Неверный или просроченный токен (401)" if resp.status_code == 401 else "Ограничение доступа или лимит запросов GitHub (403)"
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": err_reason,
                "endpoint": endpoint,
                "repository": full_name
            }
        else:
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": f"GitHub API вернул статус HTTP {resp.status_code}",
                "endpoint": endpoint,
                "repository": full_name
            }
    except Exception as e:
        return {
            "success": False,
            "status_code": 0,
            "error": f"Сетевая ошибка обращения к GitHub API: {e}",
            "endpoint": endpoint,
            "repository": full_name
        }


def get_repo_tree(repo_name: str, branch: str = None) -> dict:
    """
    READ-ONLY: Получает дерево файлов репозитория через Git Trees API.
    Корректно работает для любой ветки (main/master) и приватных репозиториев.
    """
    meta = get_repo_metadata(repo_name)
    if not meta.get("success"):
        return meta

    full_name = meta.get("full_name") or repo_name.strip()
    target_branch = branch or meta.get("default_branch") or "main"
    endpoint = f"https://api.github.com/repos/{full_name}/git/trees/{target_branch}?recursive=1"

    try:
        resp = requests.get(endpoint, headers=_get_auth_headers(), timeout=15)
        if resp.status_code == 200:
            tree_data = resp.json().get("tree", [])
            files = []
            dirs = []
            for item in tree_data:
                path = item.get("path", "")
                parts = path.replace("\\", "/").split("/")
                if any(d in IGNORE_DIRS for d in parts):
                    continue
                if item.get("type") == "blob":
                    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
                    if ext not in IGNORE_EXTENSIONS:
                        files.append(path)
                elif item.get("type") == "tree":
                    dirs.append(path)

            return {
                "success": True,
                "status_code": 200,
                "endpoint": endpoint,
                "repository": full_name,
                "branch": target_branch,
                "tree": tree_data,
                "files": files,
                "directories": dirs,
                "truncated": resp.json().get("truncated", False)
            }
        else:
            return {
                "success": False,
                "status_code": resp.status_code,
                "endpoint": endpoint,
                "repository": full_name,
                "branch": target_branch,
                "error": f"Не удалось получить git tree (HTTP {resp.status_code}) для ветки '{target_branch}'"
            }
    except Exception as e:
        return {
            "success": False,
            "status_code": 0,
            "endpoint": endpoint,
            "repository": full_name,
            "branch": target_branch,
            "error": f"Сетевая ошибка при получении git tree: {e}"
        }


def get_repo_files_list(repo_name: str, branch: str = None) -> list:
    """READ-ONLY: Возвращает список относительных путей файлов репозитория."""
    tree_res = get_repo_tree(repo_name, branch=branch)
    if tree_res.get("success"):
        return tree_res.get("files", [])
    files = get_repo_files(repo_name, branch=branch)
    return list(files.keys()) if files else []


def get_repo_file_content(repo_name: str, file_path: str, branch: str = None) -> dict:
    """
    READ-ONLY: Читает содержимое конкретного файла из репозитория GitHub.
    Использует корректные заголовки авторизации, поддерживает приватные репозитории
    и автоматически декодирует raw/base64 контент.
    """
    if not repo_name or not file_path:
        return {
            "success": False,
            "status_code": 400,
            "error": "Имя репозитория или путь к файлу не заданы",
            "repository": repo_name or "",
            "endpoint": ""
        }

    owner = GITHUB_USERNAME or "MATIN0893"
    target = repo_name.strip()
    full_name = target if "/" in target else f"{owner}/{target}"
    clean_path = file_path.strip().lstrip("/")

    target_branch = branch
    if not target_branch:
        meta = get_repo_metadata(full_name)
        target_branch = meta.get("default_branch") if meta.get("success") else "main"

    endpoint = f"https://api.github.com/repos/{full_name}/contents/{clean_path}"
    params = {"ref": target_branch}

    try:
        # 1. Попытка получить через raw заголовок
        resp = requests.get(endpoint, headers=_get_auth_headers(accept="application/vnd.github.v3.raw"), params=params, timeout=12)
        if resp.status_code == 200:
            return {
                "success": True,
                "status_code": 200,
                "content": resp.text,
                "path": clean_path,
                "repository": full_name,
                "branch": target_branch,
                "endpoint": endpoint
            }
        elif resp.status_code == 404:
            return {
                "success": False,
                "status_code": 404,
                "error": f"Файл '{clean_path}' не найден в ветке '{target_branch}' репозитория '{full_name}' (HTTP 404 Not Found)",
                "path": clean_path,
                "repository": full_name,
                "branch": target_branch,
                "endpoint": endpoint
            }
        
        # 2. Если raw не поддержан, запрашиваем обычный JSON и декодируем base64
        resp_json = requests.get(endpoint, headers=_get_auth_headers(), params=params, timeout=12)
        if resp_json.status_code == 200:
            jdata = resp_json.json()
            if isinstance(jdata, dict) and "content" in jdata and jdata.get("encoding") == "base64":
                raw_bytes = base64.b64decode(jdata["content"])
                text_content = raw_bytes.decode("utf-8", errors="ignore")
                return {
                    "success": True,
                    "status_code": 200,
                    "content": text_content,
                    "path": clean_path,
                    "repository": full_name,
                    "branch": target_branch,
                    "endpoint": endpoint
                }
            elif isinstance(jdata, dict) and "download_url" in jdata and jdata["download_url"]:
                fresp = requests.get(jdata["download_url"], headers=_get_auth_headers(), timeout=12)
                if fresp.status_code == 200:
                    return {
                        "success": True,
                        "status_code": 200,
                        "content": fresp.text,
                        "path": clean_path,
                        "repository": full_name,
                        "branch": target_branch,
                        "endpoint": endpoint
                    }

        return {
            "success": False,
            "status_code": resp.status_code,
            "error": f"GitHub API вернул статус HTTP {resp.status_code}",
            "path": clean_path,
            "repository": full_name,
            "branch": target_branch,
            "endpoint": endpoint
        }
    except Exception as e:
        return {
            "success": False,
            "status_code": 0,
            "error": f"Сетевая ошибка при чтении файла: {e}",
            "path": clean_path,
            "repository": full_name,
            "branch": target_branch,
            "endpoint": endpoint
        }


def get_user_repositories() -> list:
    """
    Делает GET к https://api.github.com/user/repos (или /users/{owner}/repos)
    с заголовками авторизации по GITHUB_TOKEN.
    """
    headers = _get_auth_headers()
    repos = []
    seen = set()

    # 1. GET https://api.github.com/user/repos
    try:
        url = "https://api.github.com/user/repos"
        params = {
            "per_page": 100,
            "sort": "updated",
            "affiliation": "owner,collaborator,organization_member"
        }
        while url:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            params = None
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for r in data:
                        name = r.get("name", "")
                        if name and name not in seen:
                            seen.add(name)
                            repos.append(RepoInfo(
                                name=name,
                                full_name=r.get("full_name", f"{GITHUB_USERNAME}/{name}" if GITHUB_USERNAME else name),
                                default_branch=r.get("default_branch", "main"),
                                html_url=r.get("html_url", f"https://github.com/{r.get('full_name', name)}"),
                                private=r.get("private", False),
                                description=r.get("description") or ""
                            ))
                if "next" in resp.links:
                    url = resp.links["next"]["url"]
                else:
                    break
            elif resp.status_code == 404:
                break
            else:
                break
    except Exception as e:
        pass

    # 2. Если пусто и указан GITHUB_USERNAME, опрашиваем /users/{owner}/repos
    owner = GITHUB_USERNAME or "MATIN0893"
    if not repos and owner:
        try:
            url = f"https://api.github.com/users/{owner}/repos"
            params = {"per_page": 100, "sort": "updated"}
            while url:
                resp = requests.get(url, headers=headers, params=params, timeout=15)
                params = None
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        for r in data:
                            name = r.get("name", "")
                            if name and name not in seen:
                                seen.add(name)
                                repos.append(RepoInfo(
                                    name=name,
                                    full_name=r.get("full_name", f"{owner}/{name}"),
                                    default_branch=r.get("default_branch", "main"),
                                    html_url=r.get("html_url", f"https://github.com/{r.get('full_name', name)}"),
                                    private=r.get("private", False),
                                    description=r.get("description") or ""
                                ))
                    if "next" in resp.links:
                        url = resp.links["next"]["url"]
                    else:
                        break
                else:
                    break
        except Exception as e:
            pass

    # 3. Fallback на PyGithub
    if not repos and Github is not None and GITHUB_TOKEN:
        try:
            gh = get_github_client()
            user = gh.get_user()
            for r in user.get_repos():
                if r.name and r.name not in seen:
                    seen.add(r.name)
                    repos.append(RepoInfo(
                        name=r.name,
                        full_name=r.full_name,
                        default_branch=getattr(r, "default_branch", "main"),
                        html_url=r.html_url,
                        private=getattr(r, "private", False),
                        description=getattr(r, "description", "") or ""
                    ))
        except Exception as e:
            pass

    return repos


def find_matching_repo(repo_name: str, available_repos: list = None) -> dict | None:
    """Нечеткий поиск (fuzzy search) подходящего существующего репозитория."""
    if not repo_name:
        return None

    if available_repos is None:
        available_repos = get_user_repositories()

    if not available_repos:
        return None

    target_clean = repo_name.strip()
    target_norm = normalize_repo_name(target_clean)

    # 1. Точное совпадение
    for r in available_repos:
        r_name = r.get("name", "")
        r_full = r.get("full_name", "")
        if r_name == target_clean or r_full == target_clean:
            return r

    # 2. Без учета регистра
    target_lower = target_clean.lower()
    for r in available_repos:
        r_name = r.get("name", "")
        r_full = r.get("full_name", "")
        if r_name.lower() == target_lower or r_full.lower() == target_lower:
            return r

    # 3. Нормализованное совпадение (без дефисов, подчеркиваний, пробелов)
    for r in available_repos:
        if normalize_repo_name(r.get("name", "")) == target_norm or normalize_repo_name(r.get("full_name", "")) == target_norm:
            return r

    # 4. Подстрока (если длина >= 4)
    if len(target_norm) >= 4:
        sub_candidates = []
        for r in available_repos:
            r_norm = normalize_repo_name(r.get("name", ""))
            if target_norm in r_norm or r_norm in target_norm:
                sub_candidates.append(r)
        if len(sub_candidates) == 1:
            return sub_candidates[0]

    # 5. Fuzzy match через SequenceMatcher
    best_match = None
    best_score = 0.0
    for r in available_repos:
        r_norm = normalize_repo_name(r.get("name", ""))
        score = difflib.SequenceMatcher(None, target_norm, r_norm).ratio()
        if score > best_score:
            best_score = score
            best_match = r

    if best_score >= 0.55 and best_match:
        return best_match

    return None


def find_matching_repo_name(repo_name: str, available_repos: list = None) -> str | None:
    matched = find_matching_repo(repo_name, available_repos)
    if matched:
        return matched.get("name")
    return None


def format_repositories_list(repos: list) -> str:
    """Форматирует аккуратный Markdown список доступных репозиториев с ссылками."""
    if not repos:
        return "📁 У тебя пока нет доступных репозиториев или не настроен токен GitHub."

    lines = ["📁 **Доступные репозитории на GitHub:**\n"]
    for r in repos:
        name = r.get("name", "")
        url = r.get("html_url") or f"https://github.com/{r.get('full_name', name)}"
        branch = r.get("default_branch", "main")
        is_private = r.get("private", False)
        lock_icon = " 🔒" if is_private else ""
        desc = f" — _{r['description']}_" if r.get("description") else ""
        lines.append(f"• [{name}]({url}) (`{branch}`){lock_icon}{desc}")

    return "\n".join(lines)


def is_repo_list_intent(prompt: str) -> bool:
    """Определяет, запрашивает ли пользователь список репозиториев."""
    if not prompt or not isinstance(prompt, str):
        return False
    text = prompt.strip().lower()
    patterns = [
        r"^(список\s+репозиториев|покажи\s+репозитории|какие\s+репозитории|мои\s+репозитории|репозитории|repos|list\s+repos)$",
        r"(список|покажи|выведи|глянь|найди)\s+.*(репозитори|проектов|реп)",
        r"(какие\s+(есть\s+)?(репозитории|проекты))",
        r"(дай|выдай)\s+список\s+(репозиториев|проектов)",
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False


def list_user_repos() -> list:
    repos = get_user_repositories()
    if repos:
        return [r["name"] for r in repos]
    return []


def get_repo_files(repo_name: str, branch: str = None, max_files: int = 50, max_size_bytes: int = MAX_FILE_SIZE_BYTES) -> dict:
    """
    Загружает файлы репозитория в память:
    1. Через Git Trees API (быстро, 1 запрос на дерево + чтение содержимого с auth-заголовками)
    2. Fallback на PyGithub
    3. Fallback на REST API с авторизацией
    """
    # 1. Быстрый и надежный способ через Git Trees API
    tree_res = get_repo_tree(repo_name, branch=branch)
    if tree_res.get("success") and tree_res.get("files"):
        target_branch = tree_res.get("branch", "main")
        file_candidates = tree_res["files"][:max_files]
        files_dict = {}
        for fpath in file_candidates:
            c_res = get_repo_file_content(repo_name, fpath, branch=target_branch)
            if c_res.get("success") and len(c_res.get("content", "")) <= max_size_bytes:
                files_dict[fpath] = c_res["content"]
        if files_dict:
            return files_dict

    # 2. PyGithub способ
    if Github is not None and GITHUB_TOKEN:
        try:
            gh = get_github_client()
            owner = GITHUB_USERNAME or "MATIN0893"
            target = repo_name.strip()
            full_name = target if "/" in target else f"{owner}/{target}"
            repo = gh.get_repo(full_name)
            target_branch = branch or getattr(repo, "default_branch", "main") or "main"

            files_dict = {}

            def fetch_recursive(path=""):
                if len(files_dict) >= max_files:
                    return
                try:
                    contents = repo.get_contents(path, ref=target_branch)
                except Exception:
                    return

                if not isinstance(contents, list):
                    contents = [contents]

                for item in contents:
                    if len(files_dict) >= max_files:
                        break
                    if item.type == "dir":
                        if item.name.lower() in IGNORE_DIRS:
                            continue
                        fetch_recursive(item.path)
                    elif item.type == "file":
                        name_lower = item.name.lower()
                        _, ext = os.path.splitext(name_lower)
                        if ext in IGNORE_EXTENSIONS or item.size > max_size_bytes:
                            continue
                        try:
                            content_str = item.decoded_content.decode("utf-8", errors="ignore")
                            files_dict[item.path] = content_str
                        except Exception:
                            pass

            fetch_recursive()
            if files_dict:
                return files_dict
        except Exception as e:
            print(f"[GitHub] PyGithub fallback error: {e}")

    # 3. Fallback на REST API
    return _get_repo_files_rest(repo_name, branch=branch, max_files=max_files, max_size_bytes=max_size_bytes)


def _get_repo_files_rest(repo_name: str, branch: str = None, max_files: int = 50, max_size_bytes: int = MAX_FILE_SIZE_BYTES) -> dict:
    """Fallback скачивания файлов через REST API GitHub с заголовками авторизации."""
    owner = GITHUB_USERNAME or "MATIN0893"
    target = repo_name.strip()
    full_name = target if "/" in target else f"{owner}/{target}"

    headers = _get_auth_headers()
    files_dict = {}

    target_branch = branch
    if not target_branch:
        meta = get_repo_metadata(full_name)
        target_branch = meta.get("default_branch") if meta.get("success") else "main"

    def fetch_dir(path=""):
        if len(files_dict) >= max_files:
            return
        url = f"https://api.github.com/repos/{full_name}/contents/{path}".rstrip("/")
        params = {"ref": target_branch}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code != 200:
                return
            items = resp.json()
            if not isinstance(items, list):
                items = [items]
            for item in items:
                if len(files_dict) >= max_files:
                    break
                itype = item.get("type")
                ipath = item.get("path", "")
                iname = item.get("name", "")
                if itype == "dir":
                    if iname.lower() in IGNORE_DIRS:
                        continue
                    fetch_dir(ipath)
                elif itype == "file":
                    name_lower = iname.lower()
                    _, ext = os.path.splitext(name_lower)
                    if ext in IGNORE_EXTENSIONS:
                        continue
                    file_res = get_repo_file_content(full_name, ipath, branch=target_branch)
                    if file_res.get("success"):
                        files_dict[ipath] = file_res["content"]
        except Exception:
            pass

    fetch_dir()
    return files_dict


def push_project(repo_name: str, files: dict, commit_message: str = "Production update by Matin Meta Agent") -> str:
    gh = get_github_client()
    user = gh.get_user()

    target_name = repo_name
    matched = find_matching_repo(repo_name)
    if matched and matched.get("name"):
        target_name = matched.get("name")

    try:
        repo = user.get_repo(target_name)
    except GithubException:
        repo = user.create_repo(target_name, private=False)

    for file_path, content in files.items():
        if not content:
            continue
        try:
            existing_file = repo.get_contents(file_path)
            repo.update_file(
                path=file_path,
                message=commit_message,
                content=content,
                sha=existing_file.sha,
            )
        except GithubException:
            repo.create_file(
                path=file_path,
                message=commit_message,
                content=content,
            )

    return repo.html_url


def delete_repo(repo_name: str) -> bool:
    gh = get_github_client()
    user = gh.get_user()
    target_name = repo_name
    matched = find_matching_repo(repo_name)
    if matched and matched.get("name"):
        target_name = matched.get("name")

    try:
        repo = user.get_repo(target_name)
        repo.delete()
        return True
    except Exception as e:
        print(f"[GitHub] Ошибка при удалении репозитория {target_name}: {e}")
        return False


def delete_repo_file(repo_name: str, file_path: str) -> bool:
    gh = get_github_client()
    user = gh.get_user()
    target_name = repo_name
    matched = find_matching_repo(repo_name)
    if matched and matched.get("name"):
        target_name = matched.get("name")

    try:
        repo = user.get_repo(target_name)
        contents = repo.get_contents(file_path)
        repo.delete_file(
            path=file_path,
            message=f"Delete {file_path} by Matin Meta Agent",
            sha=contents.sha,
        )
        return True
    except Exception as e:
        print(f"[GitHub] Ошибка при удалении файла {file_path}: {e}")
        return False
