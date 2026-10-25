"""workflow.py — تسلسل الشغل: اكتشاف → توليد → فحص → إصلاح تلقائي → تعديلات."""
from dataclasses import dataclass, field

from agents import OutputGuardrailTripwireTriggered, Runner

import config
from agent_defs import analyzer_agent, architect_agent
from db import save_message
from detector import ProjectProfile, detect_project, profile_to_text
from prompts import (build_architect_prompt, build_edit_prompt, build_fix_prompt,
                     build_guardrail_feedback)
from tools import (collect_key_files, ensure_dockerignore, extract_dockerfile,
                   list_project_files, save_dockerfile)
from verify import print_report, verify_dockerfile


@dataclass
class Session:
    user_id: str
    profile: ProjectProfile = field(default_factory=ProjectProfile)
    blueprint: str = ""
    dockerfile: str = ""
    report: dict | None = None


async def run_architect(prompt: str) -> tuple[str, list[str]]:
    """طلب للمعماري. لو الـ guardrail رفض نعيد المعماري فقط. يرجّع (dockerfile, مشاكل متبقية)."""
    last: list[str] = []
    for attempt in range(1, config.MAX_GENERATION_RETRIES + 1):
        try:
            result = await Runner.run(architect_agent, prompt)
            return extract_dockerfile(str(result.final_output)), []
        except OutputGuardrailTripwireTriggered as e:
            info = e.guardrail_result.output.output_info or {}
            last = info.get("problems") or ["unknown static-check problem"]
            print(f"\n[Guardrail] المحاولة {attempt} اترفضت: {last}")
            prompt += "\n\n" + build_guardrail_feedback(last)
    return "", last


async def verify_loop(session: Session, dockerfile: str) -> tuple[str, dict]:
    """يحفظ → يفحص فعليًا → لو فيه مشاكل يطلب إصلاح (طلب واحد لكل محاولة)."""
    report: dict = {}
    for attempt in range(config.MAX_FIX_ATTEMPTS + 1):
        path = save_dockerfile(dockerfile)
        print(f"\n[Verifying {path} ...]")
        report = verify_dockerfile(config.WEB_APP_DIR, path, session.profile)
        print_report(report)
        if report["ok"] or report["env_error"] or attempt == config.MAX_FIX_ATTEMPTS:
            break
        print(f"[Auto-fix] محاولة تصليح {attempt + 1}/{config.MAX_FIX_ATTEMPTS} (طلب واحد)")
        fixed, _ = await run_architect(
            build_fix_prompt(session.blueprint, dockerfile, report["problems"]))
        if not fixed:
            break
        dockerfile = fixed
    return dockerfile, report


async def create_first_dockerfile(session: Session) -> bool:
    profile = detect_project()
    session.profile = profile
    print(f"[Detected] {profile.summary}")
    for note in profile.notes:
        print(f"   - {note}")
    if profile.services:
        print(f"[Helper services for testing] {', '.join(s.name for s in profile.services)}")
    if ensure_dockerignore(profile.kind):
        print("[Created .dockerignore]")

    tree = list_project_files()
    if profile.kind in ("unknown", "gradle"):
        print("[Project type not fully known → asking Gemini to analyze (1 extra request)]")
        analysis = await Runner.run(analyzer_agent, f"{tree}\n\nKey files:\n{collect_key_files()}")
        session.blueprint = (profile_to_text(profile) + "\n\nANALYSIS:\n"
                             + analysis.final_output.model_dump_json(indent=2))
    else:
        session.blueprint = profile_to_text(profile)

    save_message(session.user_id, "user", "Generate initial Dockerfile")
    dockerfile, problems = await run_architect(build_architect_prompt(session.blueprint, tree))
    if not dockerfile:
        print(f"❌ الموديل ما قدرش يطلّع ملف يعدّي الفحوصات الثابتة: {problems}")
        return False

    dockerfile, report = await verify_loop(session, dockerfile)
    session.dockerfile, session.report = dockerfile, report
    save_message(session.user_id, "assistant", dockerfile)
    return True


async def apply_edit(session: Session, request: str) -> bool:
    """تعديل بطلب من المستخدم — بيمر بنفس الفحص، ولو فشل بيرجع للنسخة السابقة."""
    previous = session.dockerfile
    save_message(session.user_id, "user", request)
    new, problems = await run_architect(build_edit_prompt(session.blueprint, previous, request))
    if not new:
        print(f"❌ التعديل اترفض في الفحوصات الثابتة: {problems}")
        return False

    new, report = await verify_loop(session, new)
    if not report["ok"] and not report["env_error"]:
        save_dockerfile(previous)
        print("↩️ التعديل ما عدّاش الفحص، رجّعت النسخة السابقة (الشغّالة).")
        return False

    session.dockerfile, session.report = new, report
    save_message(session.user_id, "assistant", new)
    return True
