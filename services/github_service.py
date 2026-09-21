import os
from github import Github, GithubException
from config.settings import GITHUB_TOKEN, GITHUB_USERNAME

IGNORE_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "__pycache__",
    ".pytest_cache", ".idea", ".vscode", "node_modules", "dist", "build"
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".svg", ".webp", ".zip", ".tar", ".gz", ".exe",
    ".dll", ".so", ".dylib", ".pdf", ".woff", ".woff2", ".ttf"
}

MAX_FILE_SIZE_BYTES = 150 * 1024  # 150 KB

def get_github_client() -> Github:
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN не задан в переменных окружения.")
    return Github(GITHUB_TOKEN)

def list_user_repos() -> list:
    gh = get_github_client()
    try:
        user = gh.get_user()
        return [r.name for r in user.get_repos()]
    except Exception as e:
        print(f"[GitHub] Ошибка получения списка репозиториев: {e}")
        return []

def get_repo_files(repo_name: str) -> dict:
    gh = get_github_client()
    user = gh.get_user()
    try:
        repo = user.get_repo(repo_name)
    except GithubException:
        try:
            repo = gh.get_repo(f"{GITHUB_USERNAME}/{repo_name}")
        except Exception:
            return {}

    files_dict = {}

    def fetch_recursive(path=""):
        try:
            contents = repo.get_contents(path)
        except Exception:
            return

        if not isinstance(contents, list):
            contents = [contents]

        for item in contents:
            # Пропускаем служебные директории
            if item.type == "dir":
                if item.name.lower() in IGNORE_DIRS:
                    continue
                fetch_recursive(item.path)
            elif item.type == "file":
                # Пропускаем бинарники и мусорные расширения
                _, ext = os.path.splitext(item.name.lower())
                if ext in IGNORE_EXTENSIONS:
                    continue
                # Пропускаем слишком большие файлы
                if item.size > MAX_FILE_SIZE_BYTES:
                    continue
                try:
                    content_str = item.decoded_content.decode("utf-8", errors="ignore")
                    files_dict[item.path] = content_str
                except Exception:
                    pass

    fetch_recursive()
    return files_dict

def push_project(repo_name: str, files: dict, commit_message: str = "Production update by Matin Meta Agent") -> str:
    gh = get_github_client()
    user = gh.get_user()

    try:
        repo = user.get_repo(repo_name)
    except GithubException:
        repo = user.create_repo(repo_name, private=False)

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
    try:
        repo = user.get_repo(repo_name)
        repo.delete()
        return True
    except Exception as e:
        print(f"[GitHub] Ошибка при удалении репозитория {repo_name}: {e}")
        return False

def delete_repo_file(repo_name: str, file_path: str) -> bool:
    gh = get_github_client()
    user = gh.get_user()
    try:
        repo = user.get_repo(repo_name)
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