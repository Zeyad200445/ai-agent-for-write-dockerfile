"""llm.py — إعداد Gemini (العميل + تنظيم السرعة + ترجمة أخطاء الـ API)."""
import asyncio
import re
import time

import httpx
from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI, RateLimitError

import config

set_tracing_disabled(True)  # يقفل رسائل "OPENAI_API_KEY is not set"


class Throttle:
    """يضمن مسافة زمنية بين كل طلبين لـ Gemini (بما فيها إعادة المحاولات)."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self, request: httpx.Request):
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                print(f"   ...waiting {wait:.0f}s (free-tier rate limit)")
                await asyncio.sleep(wait)
            self._last = time.monotonic()


client = AsyncOpenAI(
    base_url=config.GEMINI_BASE_URL,
    api_key=config.GOOGLE_API_KEY or "missing-key",  # main.py بيتأكد من وجوده قبل أي طلب
    max_retries=2,
    http_client=httpx.AsyncClient(
        timeout=120,
        event_hooks={"request": [Throttle(config.MIN_REQUEST_INTERVAL)]},
    ),
)
gemini_model = OpenAIChatCompletionsModel(model=config.MODEL_NAME, openai_client=client)


def describe_api_error(e: Exception) -> str:
    """رسالة مفهومة بدل الـ traceback الطويل."""
    msg = str(e)
    if isinstance(e, RateLimitError) or "429" in msg:
        retry = re.search(r"retry in ([0-9hms\.]+)", msg)
        when = f" جرّب بعد {retry.group(1)}." if retry else ""
        if "PerDay" in msg:
            return ("❌ خلص الحد اليومي لموديل " + config.MODEL_NAME + "." + when +
                    " غيّر GEMINI_MODEL في .env لموديل تاني (الحد لكل موديل لوحده).")
        return "❌ تجاوزت حد الطلبات في الدقيقة." + when + " استنى شوية وأعد المحاولة."
    if "404" in msg or "not found" in msg.lower():
        return f"❌ الموديل '{config.MODEL_NAME}' مش موجود على مفتاحك. غيّر GEMINI_MODEL في .env."
    if "401" in msg or "403" in msg or "API key" in msg:
        return "❌ مشكلة في GOOGLE_API_KEY (غلط أو من غير صلاحية). راجع ملف .env."
    return f"❌ خطأ من الـ API: {msg[:300]}"
