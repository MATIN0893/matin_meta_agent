import ast

def validate_python(code: str) -> tuple[bool, str]:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"

def validate_project_files(files: dict) -> dict:
    errors = {}
    for filename, code in files.items():
        if filename.endswith(".py"):
            ok, err = validate_python(code)
            if not ok:
                errors[filename] = err
    return errors
