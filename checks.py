"""checks.py — فحوصات ثابتة على نص الـ Dockerfile (من غير Docker ومن غير موديل)."""
import re

_ROOT_USERS = {"root", "0"}
_SECRET_NAME = re.compile(
    r"(PASS(WORD|WD)?|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)", re.I
)
_SECRET_VALUES = [
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"ghp_[A-Za-z0-9]{30,}",
    r"AIza[0-9A-Za-z_\-]{30,}",
    r"sk-[A-Za-z0-9]{20,}",
]


def logical_lines(text: str) -> list[str]:
    """يدمج الأسطر المكمّلة بـ \\ ويشيل التعليقات والأسطر الفاضية."""
    result, buffer = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        result.append((buffer + line).strip())
        buffer = ""
    if buffer:
        result.append(buffer.strip())
    return result


def _parse_from(line: str) -> tuple[str, str | None]:
    parts = [p for p in line.split()[1:] if not p.startswith("--")]
    image = parts[0] if parts else ""
    alias = parts[2].lower() if len(parts) >= 3 and parts[1].lower() == "as" else None
    return image, alias


def _split_assignments(rest: str) -> list[tuple[str, str]]:
    """ENV A=1 B=2  أو  ENV A 1  →  [(A,1),(B,2)]"""
    tokens = rest.split()
    if tokens and "=" in tokens[0]:
        pairs = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\'[^\']*\'|\S*)', rest)
        return [(k, v.strip("\"'")) for k, v in pairs]
    parts = rest.split(None, 1)
    return [(parts[0], parts[1].strip("\"'"))] if len(parts) == 2 else []


def find_secrets(text: str) -> list[str]:
    problems = []
    for line in logical_lines(text):
        match = re.match(r"(ENV|ARG)\s+(.*)", line, re.I)
        if not match:
            continue
        for key, value in _split_assignments(match.group(2)):
            if _SECRET_NAME.search(key) and value and "$" not in value:
                problems.append(
                    f"Hardcoded secret-like value in {match.group(1).upper()} {key}. "
                    "Remove it; configuration must be injected at runtime."
                )
    for pattern in _SECRET_VALUES:
        if re.search(pattern, text):
            problems.append("The file contains what looks like a real key/token. Remove it.")
            break
    return problems


def static_problems(text: str) -> list[str]:
    """كل المشاكل اللي تتعرف من نص الملف لوحده. قايمة فاضية = سليم."""
    problems: list[str] = []
    lines = logical_lines(text)

    from_idx = [i for i, l in enumerate(lines) if re.match(r"FROM\s", l, re.I)]
    if len(from_idx) < 2:
        problems.append("Must be a multi-stage build (at least two FROM lines).")
    if not any(re.match(r"COPY\s+.*--from=", l, re.I) for l in lines):
        problems.append("Multi-stage is not really used: the final stage must `COPY --from=<stage>`.")

    # الإصدارات: ممنوع latest أو بدون tag
    stages: set[str] = set()
    for idx in from_idx:
        image, alias = _parse_from(lines[idx])
        low = image.lower()
        if low not in stages and low != "scratch" and "$" not in image and "@sha256:" not in image:
            name_part = image.rsplit("/", 1)[-1]
            if ":" not in name_part:
                problems.append(f"Base image '{image}' has no version tag. Pin an explicit version.")
            elif name_part.split(":", 1)[1].lower() == "latest":
                problems.append(f"Base image '{image}' uses :latest. Pin an explicit version.")
        if alias:
            stages.add(alias)

    # المرحلة الأخيرة: لازم USER غير root
    if from_idx:
        final = lines[from_idx[-1]:]
        users = [l.split(None, 1)[1] for l in final if re.match(r"USER\s+\S", l, re.I)]
        if not users:
            problems.append("The final stage has no USER instruction (it would run as root).")
        elif users[-1].split(":")[0].strip().lower() in _ROOT_USERS:
            problems.append("The final stage runs as root. Create and use a non-root user.")

    problems += find_secrets(text)
    return problems


# ---------- تصنيف سبب وقوع الـ container ----------
_DEPENDENCY_PATTERNS = [
    (r"UnknownHostException|Name or service not known|getaddrinfo|ENOTFOUND|could not translate host name",
     "a hostname could not be resolved (an external service like DB/cache is missing)"),
    (r"Connection refused|ECONNREFUSED|CommunicationsException|Communications link failure|"
     r"could not connect to server|Can't connect to|Unable to open JDBC Connection",
     "a connection to an external service was refused"),
    (r"Access denied for user|password authentication failed",
     "database credentials were rejected by the test database"),
]


def classify_failure(logs: str) -> str | None:
    """لو اللوج بيشاور على خدمة خارجية ناقصة يرجّع وصف، وإلا None (يعني عيب في الـ Dockerfile)."""
    for pattern, description in _DEPENDENCY_PATTERNS:
        if re.search(pattern, logs, re.I):
            return description
    return None
