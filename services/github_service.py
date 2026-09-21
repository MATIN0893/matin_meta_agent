import base64
import requests
from config.settings import GITHUB_TOKEN, GITHUB_USERNAME

def _headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

def list_user_repos(limit: int = 30) -> list:
    url = f"https://api.github.com/user/repos?per_page={limit}&sort=updated"
    resp = requests.get(url, headers=_headers(), timeout=15)
    if resp.status_code == 200:
        return [r["name"] for r in resp.json()]
    return []

def get_repo_files(repo_name: str, branch: str = "master") -> dict:
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/trees/{branch}?recursive=1"
    resp = requests.get(url, headers=_headers(), timeout=15)
    if resp.status_code != 200:
        # Fallback на main если master не существует
        url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/trees/main?recursive=1"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return {}

    tree = resp.json().get("tree", [])
    files = {}

    for item in tree:
        if item["type"] == "blob":
            path = item["path"]
            # Пропускаем служебные бинарники и кэш
            if path.startswith(".git") or "__pycache__" in path or path.endswith((".pyc", ".png", ".jpg")):
                continue
            f_resp = requests.get(item["url"], headers=_headers(), timeout=15)
            if f_resp.status_code == 200:
                raw_b64 = f_resp.json().get("content", "")
                try:
                    files[path] = base64.b64decode(raw_b64).decode("utf-8")
                except Exception:
                    pass
    return files

def push_project(repo_name: str, files: dict, branch: str = "master") -> str:
    check_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}"
    check = requests.get(check_url, headers=_headers(), timeout=15)

    if check.status_code == 404:
        create_url = "https://api.github.com/user/repos"
        requests.post(
            create_url,
            headers=_headers(),
            json={"name": repo_name, "private": False, "auto_init": True},
            timeout=15,
        )

    for path, content in files.items():
        file_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{path}"
        get_file = requests.get(file_url, headers=_headers(), timeout=15)
        sha = get_file.json().get("sha") if get_file.status_code == 200 else None

        b64_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": f"DevOps OS: update {path}",
            "content": b64_content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        requests.put(file_url, headers=_headers(), json=payload, timeout=15)

    return f"https://github.com/{GITHUB_USERNAME}/{repo_name}"

def delete_repo_file(repo_name: str, file_path: str, branch: str = "master") -> bool:
    file_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/contents/{file_path}"
    get_file = requests.get(file_url, headers=_headers(), timeout=15)
    if get_file.status_code != 200:
        return False
    sha = get_file.json().get("sha")
    payload = {
        "message": f"DevOps OS: remove {file_path}",
        "sha": sha,
        "branch": branch,
    }
    resp = requests.delete(file_url, headers=_headers(), json=payload, timeout=15)
    return resp.status_code in (200, 204)

def delete_repo(repo_name: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}"
    resp = requests.delete(url, headers=_headers(), timeout=15)
    return resp.status_code == 204

def list_branches(repo_name: str) -> list:
    """Возвращает список всех веток проекта."""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/branches"
    resp = requests.get(url, headers=_headers(), timeout=15)
    if resp.status_code == 200:
        return [b["name"] for b in resp.json()]
    return ["master"]

def create_branch(repo_name: str, new_branch: str, base_branch: str = "master") -> bool:
    """Создает новую ветку от базовой."""
    ref_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/ref/heads/{base_branch}"
    resp = requests.get(ref_url, headers=_headers(), timeout=15)
    if resp.status_code != 200:
        # Пробуем main если master нет
        ref_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/ref/heads/main"
        resp = requests.get(ref_url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return False

    sha = resp.json()["object"]["sha"]
    create_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/git/refs"
    payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
    res = requests.post(create_url, headers=_headers(), json=payload, timeout=15)
    return res.status_code == 201

def create_pull_request(repo_name: str, title: str, head_branch: str, base_branch: str = "master") -> str:
    """Создает Pull Request из head_branch в base_branch."""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/pulls"
    payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": "Автоматически сформированный PR от MATIN META AGENT OS"
    }
    resp = requests.post(url, headers=_headers(), json=payload, timeout=15)
    if resp.status_code == 201:
        return resp.json().get("html_url", "")
    return ""