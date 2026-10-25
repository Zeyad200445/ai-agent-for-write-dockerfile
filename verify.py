"""verify.py — الفحص الفعلي بـ Docker: lint، build، المستخدم، الثغرات، تشغيل + HTTP + خدمات مساعدة.
مفيهوش أي طلبات للموديل."""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import config
from checks import classify_failure

CONTAINER = "agent-app"
COMPOSE_PROJECT = "agenttest"
# قواعد hadolint اللي بنعتبرها مشاكل (غير كده بتظهر كتحذير بس)
IMPORTANT_RULES = {"DL3006", "DL3007", "DL3002", "DL3004", "DL3020"}


# ---------------------------------------------------------------- أدوات مساعدة
def _run(cmd, input_text=None, timeout=1800):
    """يرجّع (exit_code, stdout, stderr) منفصلين."""
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", "TIMEOUT"
    except FileNotFoundError:
        return 127, "", "command not found"


_START = [time.time()]


def _step(msg: str) -> None:
    """يطبع خطوة مع الوقت المنقضي، عشان تعرف إن البرنامج شغّال مش واقف."""
    print(f"  [+{int(time.time() - _START[0]):>4}s] {msg}", flush=True)


def docker_problem() -> str | None:
    if not shutil.which("docker"):
        return "Docker غير مثبّت أو مش في الـ PATH."
    code, _, _ = _run(["docker", "info"], timeout=60)
    if code != 0:
        return "Docker Desktop مش شغّال. افتحه واستنى لحد ما يشتغل وأعد التشغيل."
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tail(text: str, n: int = 2500) -> str:
    return text[-n:] if len(text) > n else text


def is_root_user(user: str) -> bool:
    return user.split(":")[0].strip().lower() in ("", "root", "0")


# ---------------------------------------------------------------- 1) lint
def lint(text: str) -> tuple[list[str], list[str]]:
    code, out, err = _run(["docker", "run", "--rm", "-i", "hadolint/hadolint",
                           "hadolint", "-f", "json", "-"], input_text=text, timeout=600)
    problems, warnings = [], []
    if not out.strip():
        if code not in (0, 1):
            warnings.append("hadolint did not run: " + _tail(err, 200))
        return problems, warnings
    try:
        items = json.loads(out)
    except json.JSONDecodeError:
        return problems, ["hadolint output could not be read"]
    for it in items:
        line = f"{it.get('code')} (line {it.get('line')}): {it.get('message')}"
        if it.get("level") == "error" or it.get("code") in IMPORTANT_RULES:
            problems.append("Lint: " + line)
        elif it.get("level") == "warning":
            warnings.append("Lint: " + line)
    return problems, warnings


# ---------------------------------------------------------------- 2) build
def build(context_dir: str, dockerfile_path: str) -> tuple[bool, str]:
    code, out, err = _run(["docker", "build", "--progress=plain", "-t", config.IMAGE_TAG,
                           "-f", dockerfile_path, context_dir], timeout=3000)
    return code == 0, out + err


def inspect_image() -> tuple[int | None, str]:
    code, out, _ = _run(["docker", "image", "inspect", config.IMAGE_TAG,
                         "--format", "{{.Size}}|{{.Config.User}}"], timeout=60)
    try:
        size, user = out.strip().split("|", 1)
        return round(int(size) / 1024 / 1024), user
    except ValueError:
        return None, ""


# ---------------------------------------------------------------- 3) Trivy
def scan_vulnerabilities() -> tuple[list[str], list[str]]:
    code, out, err = _run(
        ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
         "-v", "trivy-cache:/root/.cache/", "aquasec/trivy:latest", "image", "--quiet",
         "--format", "json", "--severity", "CRITICAL,HIGH", "--ignore-unfixed", config.IMAGE_TAG],
        timeout=1500)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [], ["Trivy scan skipped (could not run): " + _tail(err or out, 200)]
    found = {"CRITICAL": [], "HIGH": []}
    for res in data.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            if v.get("Severity") in found:
                found[v["Severity"]].append(
                    f"{v.get('VulnerabilityID')} in {v.get('PkgName')} {v.get('InstalledVersion')} "
                    f"(fixed in {v.get('FixedVersion')})")
    problems, warnings = [], []
    if found["CRITICAL"]:
        problems.append(f"{len(found['CRITICAL'])} CRITICAL fixable vulnerabilities in the image, e.g.: "
                        + "; ".join(found["CRITICAL"][:5])
                        + ". Use newer patched base image tags.")
    if found["HIGH"]:
        warnings.append(f"{len(found['HIGH'])} HIGH fixable vulnerabilities (first: {found['HIGH'][0]})")
    return problems, warnings


# ---------------------------------------------------------------- 4) تشغيل فعلي
def _q(value) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'


def _write_compose(path, host_port, port, services) -> None:
    lines = ["services:", "  app:", f"    image: {config.IMAGE_TAG}", "    pull_policy: never",
             f"    container_name: {CONTAINER}"]
    if port:
        lines += ["    ports:", f'      - "{host_port}:{port}"']
    for s in services:
        lines += [f"  {s.name}:", f"    image: {s.image}"]
        if s.env:
            lines.append("    environment:")
            lines += [f"      {k}: {_q(v)}" for k, v in s.env.items()]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _http_get(port: int) -> int | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # يتجاهل أي proxy
    try:
        with opener.open(f"http://127.0.0.1:{port}/", timeout=4) as r:
            return r.status
    except urllib.error.HTTPError as e:      # أي رد HTTP (حتى 404/500) = الخادم شغّال
        return e.code
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException, OSError):
        return None


def _is_running() -> bool:
    _, out, _ = _run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], timeout=30)
    return out.strip() == "true"


def _logs(tail: int = 40) -> str:
    _, out, err = _run(["docker", "logs", "--tail", str(tail), CONTAINER], timeout=30)
    return out + err


def _wait_services(base, services, warnings) -> None:
    for s in services:
        if not s.ready_pattern:
            continue
        _step(f"waiting for helper service '{s.name[:40]}' to be ready...")
        deadline = time.time() + 90
        while time.time() < deadline:
            _, out, err = _run(base + ["logs", s.name], timeout=60)
            if (out + err).count(s.ready_pattern) >= s.ready_count:
                break
            time.sleep(3)
        else:
            warnings.append(f"Service '{s.name}' did not report ready within 90s.")


def runtime_check(port: int | None, services) -> dict:
    """يشغّل الصورة (ومعاها الخدمات) ويشوف هل بترد على HTTP."""
    result = {"state": "not_checked", "http_code": None, "logs": "", "warnings": [], "services": []}
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    compose = os.path.abspath(os.path.join(config.TEMP_DIR, "docker-compose.test.yml"))
    host_port = _free_port() if port else None
    _write_compose(compose, host_port, port, services)
    base = ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", compose]
    _run(["docker", "rm", "-f", CONTAINER], timeout=60)
    try:
        if services:
            names = [s.name for s in services]
            code, out, err = _run(base + ["up", "-d"] + names, timeout=1500)
            if code != 0:
                result["warnings"].append("Could not start helper services: " + _tail(err or out, 500))
            else:
                result["services"] = names
                _wait_services(base, services, result["warnings"])
        code, out, err = _run(base + ["up", "-d", "app"], timeout=600)
        if code != 0:
            result.update(state="start_failed", logs=_tail(err or out))
            return result
        start = time.time()
        while time.time() - start < config.HTTP_WAIT_SECONDS:
            if not _is_running():
                result.update(state="exited", logs=_logs())
                return result
            if port:
                code = _http_get(host_port)
                if code is not None:
                    result.update(state="http", http_code=code)
                    return result
            elif time.time() - start > 10:
                result["state"] = "running_no_port"
                return result
            time.sleep(3)
        result.update(state="no_response", logs=_logs())
        return result
    finally:
        _run(base + ["down", "-v", "--remove-orphans"], timeout=300)


def _expose_port(text: str) -> int | None:
    m = re.search(r"^\s*EXPOSE\s+(\d+)", text, re.M | re.I)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- الدالة الرئيسية
def verify_dockerfile(context_dir: str, dockerfile_path: str, profile=None) -> dict:
    report = {"ok": False, "env_error": False, "build_ok": False,
              "problems": [], "warnings": [], "info": {}}
    problem = docker_problem()
    if problem:
        report["env_error"] = True
        report["problems"].append(problem)
        return report

    _START[0] = time.time()
    with open(dockerfile_path, encoding="utf-8") as f:
        text = f.read()

    _step("1/4 lint (hadolint) — أول مرة بينزّل الأداة")
    p, w = lint(text)
    report["problems"] += p
    report["warnings"] += w

    _step("2/4 docker build — أبطأ خطوة، ومفيش output لحد ما تخلص (أول مرة بتنزّل صور ومكتبات)")
    ok, out = build(context_dir, dockerfile_path)
    _step("build " + ("نجح" if ok else "فشل"))
    if not ok:
        report["problems"].append("docker build failed:\n" + _tail(out))
        return report
    report["build_ok"] = True

    size, user = inspect_image()
    report["info"]["size_mb"] = size
    report["info"]["user"] = user or "(root)"
    if is_root_user(user):
        report["problems"].append("The built image runs as root. Add a non-root USER in the final stage.")

    if config.ENABLE_TRIVY:
        _step("3/4 فحص الثغرات (Trivy) — أول مرة بينزّل قاعدة بياناته")
        p, w = scan_vulnerabilities()
        report["problems"] += p
        report["warnings"] += w

    if config.ENABLE_RUNTIME_CHECK:
        port = _expose_port(text) or (profile.port if profile else None)
        services = profile.services if profile else []
        _step("4/4 تشغيل التطبيق ومعاه الخدمات المساعدة واختبار HTTP")
        rt = runtime_check(port, services)
        _step("انتهى الفحص")
        report["warnings"] += rt["warnings"]
        if rt["services"]:
            report["info"]["helper_services"] = ", ".join(rt["services"])
        state = rt["state"]
        if state == "http":
            report["info"]["app_check"] = "http_ok"
            report["info"]["http_status"] = rt["http_code"]
        elif state in ("exited", "no_response", "start_failed"):
            dependency = classify_failure(rt["logs"])
            what = {"exited": "The container exited right after start.",
                    "no_response": f"The container is running but gave no HTTP answer on port {port} "
                                   f"within {config.HTTP_WAIT_SECONDS}s.",
                    "start_failed": "The container could not be started."}[state]
            if dependency:
                report["info"]["app_check"] = "needs_external_service"
                report["warnings"].append(f"{what} Cause looks external: {dependency}. "
                                          f"Last logs:\n{_tail(rt['logs'], 800)}")
            else:
                report["info"]["app_check"] = "failed"
                report["problems"].append(f"{what} Last logs:\n{_tail(rt['logs'])}")
        elif state == "running_no_port":
            report["info"]["app_check"] = "running_no_http"
        else:
            report["info"]["app_check"] = "not_checked"

    report["ok"] = report["build_ok"] and not report["problems"]
    return report


# ---------------------------------------------------------------- عرض النتيجة
def verdict(report: dict) -> str:
    if report["env_error"]:
        return "⚠️ ما قدرتش أفحص الملف: " + report["problems"][0]
    if not report["build_ok"]:
        return "❌ الملف ما بيتبنيش. راجع التقرير."
    if report["problems"]:
        return "❌ الملف بيتبني لكن لسه فيه مشاكل (راجع التقرير). متعتمدش عليه قبل ما تتصلح."
    check = report["info"].get("app_check")
    if check == "http_ok":
        return "✅ بيتبني، مش root، مفيش ثغرات CRITICAL قابلة للإصلاح، والتطبيق بيرد على HTTP."
    if check == "needs_external_service":
        return ("✅ بيتبني ومش root، لكن التطبيق وقف لسبب خدمة خارجية ناقصة، "
                "فمقدرتش أتأكد إنه بيرد. اختبره بالخدمات الحقيقية.")
    return "✅ بيتبني ومش root (التشغيل الفعلي لم يُفحص أو التطبيق مفيهوش HTTP)."


def print_report(report: dict) -> None:
    print("\n========== Verification Report ==========")
    print("Build:", "✅" if report["build_ok"] else "❌")
    for k, v in report["info"].items():
        print(f"  {k}: {v}")
    for p in report["problems"]:
        print("❌", p[:1200])
    for w in report["warnings"][:12]:
        print("⚠️ ", w[:600])
    print("Verdict:", verdict(report))
    print("=========================================\n")
