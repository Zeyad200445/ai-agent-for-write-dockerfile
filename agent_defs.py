"""agent_defs.py — تعريف الوكلاء (Agents) وحاجز الحماية (Guardrail) والمخرجات المهيكلة."""
from agents import Agent, GuardrailFunctionOutput, output_guardrail
from pydantic import BaseModel, Field

from checks import static_problems
from llm import gemini_model
from prompts import ANALYZER_INSTRUCTIONS, ARCHITECT_INSTRUCTIONS
from tools import extract_dockerfile


class ProjectAnalysis(BaseModel):
    language: str = Field(description="The primary programming language of the project")
    framework: str = Field(description="The main framework used")
    build_tools: str = Field(description="The build tool used if any")
    build_command: str = Field(description="The command to compile or build the project")
    start_command: str = Field(description="The command to start the application in production")
    exposed_ports: list[int] = Field(description="A list of ports the application listens on")


@output_guardrail
async def dockerfile_guardrail(ctx, agent, output):
    """يرفض أي Dockerfile مخالف للقواعد الثابتة (multi-stage / root / latest / أسرار)."""
    problems = static_problems(extract_dockerfile(str(output)))
    return GuardrailFunctionOutput(
        output_info={"problems": problems},
        tripwire_triggered=bool(problems),
    )


# بيتستخدم بس لو نوع المشروع مش معروف للكود (طلب إضافي واحد)
analyzer_agent = Agent(
    name="Code Analyzer",
    instructions=ANALYZER_INSTRUCTIONS,
    model=gemini_model,
    output_type=ProjectAnalysis,
)

architect_agent = Agent(
    name="Docker Architect",
    instructions=ARCHITECT_INSTRUCTIONS,
    model=gemini_model,
    output_guardrails=[dockerfile_guardrail],
)
