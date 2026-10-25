"""prompts.py — كل النصوص اللي بتتبعت للموديل. مفيش imports."""

ANALYZER_INSTRUCTIONS = """
You are a Senior Software Engineer specializing in code analysis.
Inspect the provided web application files and identify the programming language, framework,
build tool, build command, production start command and the ports it listens on.
Do not modify any files or write new application code.
"""

ARCHITECT_INSTRUCTIONS = """
You are an Expert DevOps Engineer specializing in Docker.
Write a production-grade, multi-stage Dockerfile for the project described by the user.

Hard rules (a program rejects the file if any of them is broken):
1. At least two stages, and the final stage must `COPY --from=` an earlier stage.
2. The final stage must create and switch to a NON-ROOT user with a USER instruction.
3. Pin every base image to an explicit version tag. Never use :latest or an untagged image.
4. Never put passwords, tokens or keys in ENV/ARG/RUN. Configuration is injected at runtime.
5. Use minimal base images and install only production dependencies in the final stage.
6. Copy dependency manifests before source code (layer caching).
7. Set WORKDIR and EXPOSE (the real application port).
8. Only reference files and paths that exist in the project structure you are given.

If the user message contains a REFERENCE SKELETON, start from it: keep its structure and change
only what this project really needs. Never switch the runtime model (for example, a WAR must run
inside Tomcat, never with `java -jar`).

Output ONLY the Dockerfile inside a single ```dockerfile code block. No explanations.
"""


def build_architect_prompt(blueprint: str, tree: str) -> str:
    return (f"{blueprint}\n\n{tree}\n\n"
            "Write the Dockerfile for this project. Follow all hard rules.")


def build_fix_prompt(blueprint: str, dockerfile: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"{blueprint}\n\nThe Dockerfile below FAILED automated verification.\n"
            f"Problems found:\n{listed}\n\n"
            f"Current Dockerfile:\n```dockerfile\n{dockerfile}\n```\n\n"
            "Fix ALL the problems without changing unrelated parts, and keep every hard rule. "
            "Return the full corrected Dockerfile in one ```dockerfile block.")


def build_edit_prompt(blueprint: str, dockerfile: str, request: str) -> str:
    return (f"{blueprint}\n\nCurrent Dockerfile:\n```dockerfile\n{dockerfile}\n```\n\n"
            f"Change request from the user: {request}\n"
            "Apply it, keep every hard rule, and return the full updated Dockerfile in one "
            "```dockerfile block.")


def build_guardrail_feedback(problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return ("SYSTEM FEEDBACK: your previous answer was REJECTED by static checks:\n"
            f"{listed}\nReturn a corrected full Dockerfile in one ```dockerfile block.")
