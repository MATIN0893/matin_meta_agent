from github import Github
from config.settings import GITHUB_TOKEN

g = Github(GITHUB_TOKEN)

def list_user_repos(limit: int = 15) -> list[str]:
    """Возвращает список имен твоих репозиториев."""
    user = g.get_user()
    repos = user.get_repos(sort="updated", direction="desc")
    return [repo.name for repo in list(repos)[:limit]]

def get_repo_files(project_name: str) -> dict[str, str]:
    """Скачивает все текстовые файлы из репозитория."""
    user = g.get_user()
    repo = user.get_repo(project_name)
    files = {}

    contents = repo.get_contents("")
    while contents:
        file_content = contents.pop(0)
        if file_content.type == "dir":
            contents.extend(repo.get_contents(file_content.path))
        else:
            try:
                files[file_content.path] = file_content.decoded_content.decode("utf-8")
            except Exception:
                pass
    return files

def push_project(project_name: str, files: dict) -> str:
    user = g.get_user()

    try:
        repo = user.get_repo(project_name)
    except:
        repo = user.create_repo(
            project_name,
            description="Generated/Updated by Matin Meta Agent",
            private=False,
            auto_init=False
        )

    for filename, content in files.items():
        try:
            existing = repo.get_contents(filename)
            repo.update_file(filename, f"Update {filename}", content, existing.sha)
        except:
            repo.create_file(filename, f"Add {filename}", content)

    return repo.html_url

def delete_repo_file(project_name: str, filename: str) -> bool:
    """Удаляет отдельный файл из репозитория."""
    user = g.get_user()
    try:
        repo = user.get_repo(project_name)
        existing = repo.get_contents(filename)
        repo.delete_file(filename, f"Delete {filename}", existing.sha)
        return True
    except Exception:
        return False

def delete_repo(project_name: str) -> bool:
    """Полностью удаляет репозиторий с GitHub."""
    user = g.get_user()
    try:
        repo = user.get_repo(project_name)
        repo.delete()
        return True
    except Exception:
        return False