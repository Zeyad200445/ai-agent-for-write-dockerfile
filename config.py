"""config.py — كل الإعدادات في مكان واحد. مفيش imports من ملفات المشروع."""
import os

from dotenv import load_dotenv

load_dotenv(override=True)


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---------- Gemini ----------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
# الحد المجاني = 5 طلبات/دقيقة، يعني طلب كل 12 ثانية على الأقل
MIN_REQUEST_INTERVAL = float(os.getenv("MIN_REQUEST_INTERVAL", "12.5"))

# ---------- المشروع ----------
WEB_APP_DIR = os.getenv("WEB_APP_DIR", "./vprofile-project")
DB_PATH = "chat_sessions.db"
TEMP_DIR = ".agent_tmp"          # ملفات الفحص المؤقتة (docker-compose للاختبار)
IMAGE_TAG = "agent-dockerfile-test:latest"

# ---------- حدود المحاولات (كل محاولة = طلب من الـ quota) ----------
MAX_GENERATION_RETRIES = 3       # لو الـ guardrail رفض الشكل
MAX_FIX_ATTEMPTS = 2             # لو الفحص الفعلي (build/run/scan) لقى مشاكل

# ---------- الفحوصات (تقدر تقفلها من .env) ----------
ENABLE_TRIVY = _flag("ENABLE_TRIVY", "1")        # فحص الثغرات
ENABLE_RUNTIME_CHECK = _flag("ENABLE_RUNTIME_CHECK", "1")  # تشغيل + HTTP + خدمات
HTTP_WAIT_SECONDS = int(os.getenv("HTTP_WAIT_SECONDS", "90"))

# ---------- قراءة المشروع ----------
SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", "__pycache__",
    ".idea", ".vscode", "venv", ".venv", TEMP_DIR,
}
KEY_FILE_NAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json",
    "requirements.txt", "pyproject.toml", "go.mod", "Gemfile",
    "composer.json", "application.properties", "web.xml",
}
