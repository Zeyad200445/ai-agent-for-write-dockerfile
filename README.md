# AI Agent for Writing Dockerfiles

An AI agent that writes a **multi-stage, non-root Dockerfile** for your project and then **proves it works**
by building and running it with Docker. If the Dockerfile fails verification, the agent feeds the
exact errors back to the model and tries again.

Most "AI writes a Dockerfile" tools stop at generating text. This one verifies the result.

## What it does

1. **Detects the project type from your build files (plain code, no LLM guessing):**
   Maven (WAR, JAR, Spring Boot), Node (server apps and static front-ends), Python, Go.
   Gradle and unknown projects fall back to an LLM analysis step.
2. **Picks the right runtime from evidence.** For example, a WAR is run inside Tomcat (never with
   `java -jar`), and Tomcat 9 vs 10.1 is chosen from the Spring / Spring Boot version in `pom.xml`.
3. **Asks Gemini to write the Dockerfile** starting from a reference skeleton for the detected type.
4. **Rejects bad output with static guardrails:** not multi-stage, final stage runs as root,
   `:latest` or untagged base images, hardcoded secrets in `ENV`/`ARG`.
5. **Verifies it for real with Docker:**
   - lint with hadolint
   - `docker build`
   - checks the user the built image really runs as
   - vulnerability scan with Trivy (optional)
   - runs the app together with helper services it needs (MySQL, PostgreSQL, Memcached,
     RabbitMQ, Redis, MongoDB) and sends an HTTP request
6. **Fixes itself:** build errors, root user, crashes and critical vulnerabilities are sent back to
   the model (max 2 fix attempts). If a crash is caused by a missing external service, it is reported
   as a warning instead of wasting requests.
7. **Safe edits:** you can ask for changes in a chat loop. Every edit goes through the same checks,
   and a failing edit is rolled back to the last working Dockerfile.
8. Creates a `.dockerignore` if the project does not have one.

It is designed for the Gemini free tier: requests are throttled, most runs use 1 to 3 requests,
and API errors (daily limit, wrong model, bad key) are shown as clear messages.

## Requirements

- Python 3.10+
- Docker Desktop (running) with `docker compose`
- A Google Gemini API key

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then edit .env
```

Minimal `.env`:

```
GOOGLE_API_KEY=your_key
GEMINI_MODEL=gemini-3.1-flash-lite-preview
WEB_APP_DIR=/full/path/to/your/project
```

## Usage

```bash
python main.py
```

The generated file is saved to `<WEB_APP_DIR>/Dockerfile` (an existing one is overwritten, so back it up).
After the first result you can type change requests, or `exit` to quit.

The first run is slow (several minutes) because Docker has to download images and dependencies.
Later runs reuse the cache.

## Configuration (.env)

- `GOOGLE_API_KEY`: your Gemini API key (required)
- `GEMINI_MODEL`: model name. Free-tier limits apply per model, so switch models if one runs out
- `WEB_APP_DIR`: path to the project you want a Dockerfile for
- `MIN_REQUEST_INTERVAL`: seconds between API requests (default 12.5, matches 5 requests/minute)
- `ENABLE_TRIVY`: `1` or `0`, vulnerability scan (default 1)
- `ENABLE_RUNTIME_CHECK`: `1` or `0`, run the container and probe HTTP (default 1)
- `HTTP_WAIT_SECONDS`: how long to wait for the app to answer (default 90)

## Project structure

- `main.py`: entry point and chat loop
- `workflow.py`: generate, verify, auto-fix and edit flow
- `agent_defs.py`: agents and the output guardrail
- `llm.py`: Gemini client, request throttling, readable API errors
- `prompts.py`: all prompt texts
- `detector.py`: project type, Java/Tomcat version and helper-service detection
- `checks.py`: static Dockerfile checks and crash-cause classification
- `verify.py`: Docker-based verification (lint, build, scan, runtime probe)
- `tools.py`: file helpers (read project, save Dockerfile, `.dockerignore`)
- `db.py`: SQLite chat log
- `config.py`: settings loaded from `.env`

## Limitations

- It verifies that the image builds, runs as non-root, and answers HTTP. It does **not** test your
  application logic.
- If your app is configured to use `localhost` for its database, the helper services cannot help.
- Review the Dockerfile before using it in production.
- By default only a project description, the file/folder names and a reference skeleton are sent to the model.
  For unknown or Gradle projects, key config files (for example `application.properties`) are also sent
  for analysis, so remove secrets from them first.
- Test credentials found in your project's config files are written to `.agent_tmp/` for the local
  test run. That folder is git-ignored. Do not publish it.
- On Windows, Trivy needs the Docker socket mounted. If that fails, set `ENABLE_TRIVY=0`.
