"""detector.py — يكتشف نوع المشروع بقواعد ثابتة (بالكود) بدل ما الموديل يخمّن."""
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import config
from tools import collect_config_text, find_file, read_text

LTS_JAVA = [8, 11, 17, 21]


@dataclass
class ServiceSpec:
    """خدمة مساعدة بيحتاجها التطبيق (قاعدة بيانات/كاش...) عشان نختبر بيها."""
    name: str                      # اسم الـ host اللي التطبيق بيدوّر عليه
    image: str
    env: dict = field(default_factory=dict)
    ready_pattern: str | None = None
    ready_count: int = 1


@dataclass
class ProjectProfile:
    kind: str = "unknown"          # maven-war | maven-springboot-jar | maven-jar | gradle | node | node-static | python | go | unknown
    summary: str = "Unknown project type"
    port: int | None = None
    skeleton: str = ""
    notes: list[str] = field(default_factory=list)
    services: list[ServiceSpec] = field(default_factory=list)


# ======================= Maven =======================
def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _text(el: ET.Element | None, tag: str) -> str:
    child = el.find(tag) if el is not None else None
    return (child.text or "").strip() if child is not None and child.text else ""


def _resolve(value: str, props: dict) -> str:
    for _ in range(3):
        value = re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), value)
    return value


def _lts_for(version: int) -> int:
    for lts in LTS_JAVA:
        if lts >= version:
            return lts
    return LTS_JAVA[-1]


def _major(value: str) -> int | None:
    m = re.match(r"\s*(\d+)", value or "")
    return int(m.group(1)) if m else None


def _choose_tomcat(root: ET.Element, props: dict, raw: str) -> tuple[str, str]:
    """يختار Tomcat بناءً على أدلة حقيقية: إصدار Spring Boot parent، إصدار Spring، أو اسم الـ servlet API."""
    parent = root.find("parent")
    boot = _major(_text(parent, "version")) if parent is not None and \
        _text(parent, "artifactId") == "spring-boot-starter-parent" else None
    spring = _major(_resolve(props.get("spring.version") or props.get("spring-framework.version") or "", props))
    if boot is not None and boot >= 3:
        return "10.1", f"Spring Boot parent {boot}.x requires jakarta.*"
    if spring is not None and spring >= 6:
        return "10.1", f"Spring Framework {spring}.x requires jakarta.*"
    if "jakarta.servlet" in raw:
        return "10.1", "pom.xml depends on jakarta.servlet"
    if boot is not None:
        return "9.0", f"Spring Boot parent {boot}.x uses javax.*"
    if spring is not None:
        return "9.0", f"Spring Framework {spring}.x uses javax.*"
    if "javax.servlet" in raw:
        return "9.0", "pom.xml depends on javax.servlet"
    return "9.0", "NO clear evidence in pom.xml; assumed javax (Tomcat 9). If the app imports jakarta.*, use Tomcat 10.1"


def _java_version(root: ET.Element, props: dict) -> int:
    candidates = [props.get(k) for k in
                  ("java.version", "maven.compiler.release", "maven.compiler.source", "maven.compiler.target")]
    for plugin in root.iter("plugin"):
        if _text(plugin, "artifactId") == "maven-compiler-plugin":
            cfg = plugin.find("configuration")
            candidates += [_text(cfg, "release"), _text(cfg, "source"), _text(cfg, "target")]
    for raw in candidates:
        if not raw:
            continue
        raw = _resolve(raw, props)
        match = re.match(r"^(?:1\.)?(\d+)", raw)
        if match:
            return _lts_for(int(match.group(1)))
    return 17


MAVEN_WAR_SKELETON = """\
FROM maven:3.9-eclipse-temurin-{jdk} AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -B -q dependency:go-offline
COPY src ./src
RUN mvn -B -q clean package -DskipTests

FROM tomcat:{tomcat}-jdk{rt}-temurin
RUN rm -rf /usr/local/tomcat/webapps/* \\
 && groupadd -r app && useradd -r -g app app
COPY --from=build /build/target/*.war /usr/local/tomcat/webapps/ROOT.war
RUN chown -R app:app /usr/local/tomcat
USER app
EXPOSE 8080
CMD ["catalina.sh", "run"]
"""

MAVEN_JAR_SKELETON = """\
FROM maven:3.9-eclipse-temurin-{jdk} AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -B -q dependency:go-offline
COPY src ./src
RUN mvn -B -q clean package -DskipTests

FROM eclipse-temurin:{jdk}-jre-alpine
WORKDIR /app
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /build/target/*.jar app.jar
USER app
EXPOSE {port}
ENTRYPOINT ["java", "-jar", "app.jar"]
"""


def _detect_maven(pom_path: str, cfg_text: str) -> ProjectProfile | None:
    try:
        root = ET.parse(pom_path).getroot()
    except ET.ParseError:
        return None
    _strip_ns(root)
    props_el = root.find("properties")
    props = {c.tag: (c.text or "").strip() for c in props_el} if props_el is not None else {}
    packaging = _text(root, "packaging") or "jar"
    artifact = _text(root, "artifactId")
    version = _resolve(_text(root, "version"), props)
    final_name = _resolve(_text(root.find("build"), "finalName"), props)
    raw = ET.tostring(root, encoding="unicode")
    boot = "spring-boot-maven-plugin" in raw or "spring-boot-starter-parent" in raw
    jdk = _java_version(root, props)

    notes = [f"Java version detected from pom.xml: {jdk}",
             f"Build context is the project root; pom.xml is at: {os.path.relpath(pom_path, config.WEB_APP_DIR)}"]
    if os.path.dirname(os.path.abspath(pom_path)) != os.path.abspath(config.WEB_APP_DIR):
        notes.append("WARNING: pom.xml is not at the project root; adjust COPY paths accordingly.")
    expected = final_name or (f"{artifact}-{version}" if artifact and version else artifact)

    if packaging == "war":
        tomcat, evidence = _choose_tomcat(root, props, raw)
        rt = max(jdk, 11) if tomcat == "10.1" else jdk
        notes += ["Packaging is WAR: it MUST run inside Tomcat. `java -jar` on a WAR fails with 'no main manifest attribute'.",
                  f"Tomcat {tomcat} chosen ({'jakarta' if tomcat == '10.1' else 'javax'} servlet API). Evidence: {evidence}",
                  f"Expected artifact: target/{expected}.war (skeleton uses a wildcard)."]
        return ProjectProfile(
            kind="maven-war", port=8080,
            summary=f"Maven WAR (Java {jdk}, Tomcat {tomcat})",
            skeleton=MAVEN_WAR_SKELETON.format(jdk=jdk, tomcat=tomcat, rt=rt), notes=notes)

    port_match = re.search(r"server\.port\s*[=:]\s*(\d+)", cfg_text)
    port = int(port_match.group(1)) if port_match else 8080
    kind = "maven-springboot-jar" if boot else "maven-jar"
    if not boot:
        notes.append("Plain jar: make sure the jar has a Main-Class; otherwise set the right main class in CMD.")
    notes.append(f"Expected artifact: target/{expected}.jar (skeleton uses a wildcard).")
    return ProjectProfile(
        kind=kind, port=port,
        summary=f"Maven {'Spring Boot ' if boot else ''}JAR (Java {jdk}, port {port})",
        skeleton=MAVEN_JAR_SKELETON.format(jdk=jdk, port=port), notes=notes)


# ======================= Node =======================
NODE_SKELETON = """\
FROM node:{node}-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .
{build}RUN npm prune --omit=dev

FROM node:{node}-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE {port}
CMD {cmd}
"""

NODE_STATIC_SKELETON = """\
FROM node:{node}-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/{outdir} /usr/share/nginx/html
USER 101
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
"""


def _detect_node(pkg_path: str) -> ProjectProfile | None:
    try:
        pkg = json.loads(read_text(pkg_path))
    except json.JSONDecodeError:
        return None
    scripts = pkg.get("scripts", {}) or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    engines = (pkg.get("engines") or {}).get("node", "")
    m = re.search(r"(\d+)", engines)
    node = m.group(1) if m and m.group(1) in ("22", "24") else "22"
    folder = os.path.dirname(pkg_path)
    has_lock = os.path.exists(os.path.join(folder, "package-lock.json"))
    install = "npm ci" if has_lock else "npm install"
    notes = [f"Node version: {node}", "No package-lock.json: using `npm install`." if not has_lock else "Lockfile found: using `npm ci`."]

    server_libs = {"express", "fastify", "koa", "@nestjs/core", "next", "hapi", "@hapi/hapi"}
    is_static = bool(({"vite", "react-scripts"} & set(deps)) and not (server_libs & set(deps)))
    if is_static:
        outdir = "build" if "react-scripts" in deps else "dist"
        notes.append(f"Static front-end: build output folder assumed '{outdir}'. Served by unprivileged nginx on 8080.")
        return ProjectProfile(
            kind="node-static", port=8080, summary=f"Node static front-end (Node {node}, nginx)",
            skeleton=NODE_STATIC_SKELETON.format(node=node, install=install, outdir=outdir), notes=notes)

    main = pkg.get("main") or "server.js"
    port = 3000
    for candidate in {main, "server.js", "app.js", "index.js", "main.js"}:
        src = read_text(os.path.join(folder, candidate), 20000)
        m2 = re.search(r"(?:PORT\s*\|\|\s*|listen\(\s*)(\d{4,5})", src)
        if m2:
            port = int(m2.group(1))
            break
    cmd = '["npm", "start"]' if "start" in scripts else f'["node", "{main}"]'
    build = "RUN npm run build\n" if "build" in scripts else ""
    notes.append(f"Start command: {cmd}; port guessed: {port} (verify in source).")
    return ProjectProfile(
        kind="node", port=port, summary=f"Node app (Node {node}, port {port})",
        skeleton=NODE_SKELETON.format(node=node, install=install, build=build, port=port, cmd=cmd), notes=notes)


# ======================= Python =======================
PY_SKELETON = """\
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app
COPY --from=build /install /usr/local
COPY --chown=app:app . .
USER app
EXPOSE {port}
CMD {cmd}
"""


def _detect_python(folder: str) -> ProjectProfile:
    req = read_text(os.path.join(folder, "requirements.txt")).lower()
    notes = []
    port, cmd = 8000, '["python", "app.py"]'
    entry = next((f for f in ("main.py", "app.py", "server.py") if os.path.exists(os.path.join(folder, f))), None)
    if "fastapi" in req:
        module = (entry or "main.py")[:-3]
        cmd = f'["uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "8000"]'
        notes.append("FastAPI detected: requirements must include uvicorn; verify the app object name.")
    elif "django" in req:
        cmd = '["python", "manage.py", "runserver", "0.0.0.0:8000"]'
        notes.append("Django detected: for production prefer gunicorn <project>.wsgi (add it to requirements).")
    elif "flask" in req:
        port = 5000
        cmd = '["gunicorn", "-b", "0.0.0.0:5000", "' + (entry or "app.py")[:-3] + ':app"]'
        notes.append("Flask detected: add gunicorn to requirements; verify the app object name.")
    elif entry:
        cmd = f'["python", "{entry}"]'
    notes.append("Python 3.12 assumed unless the project says otherwise.")
    has_req = os.path.exists(os.path.join(folder, "requirements.txt"))
    if not has_req:
        notes.append("No requirements.txt: dependencies come from pyproject.toml/Pipfile; adapt the install step.")
    return ProjectProfile(kind="python", port=port, summary=f"Python app (entry: {entry or 'unknown'}, port {port})",
                          skeleton=PY_SKELETON.format(port=port, cmd=cmd), notes=notes)


# ======================= Go =======================
GO_SKELETON = """\
FROM golang:{go}-alpine AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /out/server .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server
USER nonroot:nonroot
EXPOSE {port}
ENTRYPOINT ["/server"]
"""


def _detect_go(mod_path: str) -> ProjectProfile:
    m = re.search(r"^go\s+(\d+\.\d+)", read_text(mod_path), re.M)
    go = m.group(1) if m else "1.23"
    folder = os.path.dirname(mod_path)
    port = 8080
    src = read_text(os.path.join(folder, "main.go"), 20000)
    m2 = re.search(r'":(\d{4,5})"', src)
    if m2:
        port = int(m2.group(1))
    notes = [f"Go version from go.mod: {go}", "Main package assumed in the project root; if it is under cmd/<name>, change the build path."]
    return ProjectProfile(kind="go", port=port, summary=f"Go app (Go {go}, port {port})",
                          skeleton=GO_SKELETON.format(go=go, port=port), notes=notes)


# ======================= الخدمات المساعدة =======================
# اسم → (كلمات دالة، image، pattern الجاهزية، عدد المرات، regex لاستخراج الـ host)
_CATALOG = {
    "mysql": (("mysql",), "mysql:8.0", "port: 3306", 1, r"jdbc:mysql://([\w.\-]+)"),
    "postgres": (("postgresql", "psycopg", "asyncpg"), "postgres:16-alpine", "ready to accept connections", 2, r"jdbc:postgresql://([\w.\-]+)"),
    "memcached": (("memcached",), "memcached:1.6-alpine", None, 1, r"memcached[\w.]*host\s*[=:]\s*([\w.\-]+)"),
    "rabbitmq": (("rabbitmq", "amqp"), "rabbitmq:3-alpine", "Server startup complete", 1, r"rabbit(?:mq)?[\w.]*(?:address|host)\s*[=:]\s*([\w.\-]+)"),
    "redis": (("redis", "jedis", "lettuce", "ioredis"), "redis:7-alpine", None, 1, r"redis[\w.]*host\s*[=:]\s*([\w.\-]+)"),
    "mongodb": (("mongodb", "mongoose", "pymongo"), "mongo:7", "Waiting for connections", 1, r"mongodb://(?:[^@/\s]+@)?([\w.\-]+)"),
}


def detect_services(blob: str) -> list[ServiceSpec]:
    low = blob.lower()
    user = (re.search(r"(?:jdbc|datasource)\.?username\s*[=:]\s*(\S+)", blob, re.I) or [None, None])[1]
    pwd = (re.search(r"(?:jdbc|datasource)\.?password\s*[=:]\s*(\S+)", blob, re.I) or [None, None])[1]
    dbname = (re.search(r"jdbc:\w+://[\w.\-]+(?::\d+)?/(\w+)", blob) or [None, None])[1]
    found: list[ServiceSpec] = []
    for key, (words, image, ready, count, host_re) in _CATALOG.items():
        if not any(w in low for w in words):
            continue
        m = re.search(host_re, blob, re.I)
        host = m.group(1) if m and m.group(1) not in ("localhost", "127.0.0.1") else key
        env: dict = {}
        if key == "mysql":
            env["MYSQL_ROOT_PASSWORD"] = pwd or "root"
            if dbname:
                env["MYSQL_DATABASE"] = dbname
            if user and user != "root":
                env["MYSQL_USER"], env["MYSQL_PASSWORD"] = user, pwd or "root"
        elif key == "postgres":
            env = {"POSTGRES_PASSWORD": pwd or "postgres", "POSTGRES_USER": user or "postgres"}
            if dbname:
                env["POSTGRES_DB"] = dbname
        found.append(ServiceSpec(host, image, env, ready, count))
    return found


# ======================= الدالة الرئيسية =======================
def detect_project() -> ProjectProfile:
    cfg_text = collect_config_text()
    profile: ProjectProfile | None = None
    others: list[str] = []

    pom, gradle = find_file("pom.xml"), find_file("build.gradle") or find_file("build.gradle.kts")
    pkg, gomod = find_file("package.json"), find_file("go.mod")
    req = find_file("requirements.txt") or find_file("pyproject.toml")

    blob = cfg_text
    if pom:
        profile = _detect_maven(pom, cfg_text)
        blob += read_text(pom)
        others = [n for n, p in (("package.json", pkg), ("build.gradle", gradle)) if p]
    elif gradle:
        profile = ProjectProfile(kind="gradle", summary="Gradle project (no ready skeleton)",
                                 notes=["Gradle project: detect Java version and artifact from build.gradle yourself."])
        blob += read_text(gradle)
    elif pkg:
        profile = _detect_node(pkg)
        blob += read_text(pkg)
    elif gomod:
        profile = _detect_go(gomod)
    elif req:
        profile = _detect_python(os.path.dirname(req))
        blob += read_text(req)

    profile = profile or ProjectProfile()
    if others:
        profile.notes.append(f"Other build files also found: {', '.join(others)} (ignored).")
    if profile.kind != "unknown":
        profile.services = detect_services(blob)
    return profile


def profile_to_text(profile: ProjectProfile) -> str:
    """النص اللي بنبعته للموديل كـ blueprint."""
    parts = [f"PROJECT TYPE (detected by static analysis, do not contradict it): {profile.summary}"]
    if profile.port:
        parts.append(f"Application port: {profile.port}")
    if profile.notes:
        parts.append("NOTES:\n" + "\n".join(f"- {n}" for n in profile.notes))
    if profile.services:
        parts.append("The app talks to external services (provided separately at runtime, "
                     "do NOT install them in the image): " + ", ".join(s.name for s in profile.services))
    if profile.skeleton:
        parts.append("REFERENCE SKELETON (start from this, adapt only what the project needs):\n```dockerfile\n"
                     + profile.skeleton.strip() + "\n```")
    return "\n\n".join(parts)
