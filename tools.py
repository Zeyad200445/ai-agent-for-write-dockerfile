"""tools.py — دوال التعامل مع الملفات (قراءة المشروع، حفظ Dockerfile، .dockerignore)."""
import os
import re
from collections import deque

import config


def read_text(path: str, limit: int | None = None) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read() if limit is None else f.read(limit)
    except OSError:
        return ""


def find_file(name: str) -> str | None:
    """يدوّر على أقرب ملف بالاسم ده (يبدأ من جذر المشروع)."""
    queue = deque([config.WEB_APP_DIR])
    while queue:
        folder = queue.popleft()
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        if name in entries and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
        for entry in entries:
            path = os.path.join(folder, entry)
            if os.path.isdir(path) and entry not in config.SKIP_DIRS:
                queue.append(path)
    return None


def list_project_files(max_entries: int = 400) -> str:
    """هيكل شجري بأسماء الملفات والمجلدات."""
    if not os.path.exists(config.WEB_APP_DIR):
        return ""
    lines, count = ["Project Structure:"], 0
    for root, dirs, files in os.walk(config.WEB_APP_DIR):
        dirs[:] = sorted(d for d in dirs if d not in config.SKIP_DIRS)
        level = os.path.relpath(root, config.WEB_APP_DIR).count(os.sep)
        level = 0 if root == config.WEB_APP_DIR else level + 1
        lines.append(f"{'    ' * level}{os.path.basename(root)}/")
        for f in sorted(files):
            lines.append(f"{'    ' * (level + 1)}{f}")
            count += 1
            if count >= max_entries:
                lines.append("    ... (truncated)")
                return "\n".join(lines)
    return "\n".join(lines)


def collect_key_files(max_total: int = 24000, per_file: int = 6000) -> str:
    """محتوى الملفات المهمة (pom.xml, package.json...)."""
    out, total = "", 0
    for root, dirs, files in os.walk(config.WEB_APP_DIR):
        dirs[:] = [d for d in dirs if d not in config.SKIP_DIRS]
        for f in files:
            if f in config.KEY_FILE_NAMES:
                path = os.path.join(root, f)
                chunk = (
                    f"\n--- {os.path.relpath(path, config.WEB_APP_DIR)} ---\n"
                    f"{read_text(path, per_file)}\n"
                )
                if total + len(chunk) > max_total:
                    return out
                out += chunk
                total += len(chunk)
    return out


def collect_config_text(max_total: int = 60000) -> str:
    """كل ملفات الإعدادات (.properties/.yml/.yaml) مدموجة في نص واحد."""
    out = ""
    for root, dirs, files in os.walk(config.WEB_APP_DIR):
        dirs[:] = [d for d in dirs if d not in config.SKIP_DIRS]
        for f in files:
            if f.endswith((".properties", ".yml", ".yaml")):
                out += "\n" + read_text(os.path.join(root, f), 8000)
                if len(out) > max_total:
                    return out[:max_total]
    return out


# ---------- Dockerfile ----------
def extract_dockerfile(text: str) -> str:
    """يستخرج كود الـ Dockerfile من داخل ```dockerfile ... ```"""
    match = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    return (match.group(1) if match else text).strip()


def save_dockerfile(content: str) -> str:
    path = os.path.join(config.WEB_APP_DIR, "Dockerfile")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content.strip() + "\n")
    return os.path.abspath(path)


_DOCKERIGNORE_COMMON = [".git", ".gitignore", ".idea", ".vscode", ".env", "*.log",
                        config.TEMP_DIR, "docker-compose*.yml"]
_DOCKERIGNORE_BY_KIND = {
    "maven": ["target/"],
    "gradle": ["build/", ".gradle/"],
    "node": ["node_modules", "npm-debug.log"],
    "python": ["__pycache__", "*.pyc", ".venv", "venv"],
    "go": ["bin/"],
}


def ensure_dockerignore(kind: str) -> bool:
    """يعمل .dockerignore لو مش موجود (بالكود، من غير طلب للموديل). True لو اتعمل."""
    path = os.path.join(config.WEB_APP_DIR, ".dockerignore")
    if os.path.exists(path):
        return False
    family = kind.split("-")[0]
    lines = _DOCKERIGNORE_COMMON + _DOCKERIGNORE_BY_KIND.get(family, [])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return True
