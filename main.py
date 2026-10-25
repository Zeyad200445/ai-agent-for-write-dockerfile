"""main.py — نقطة التشغيل: python main.py"""
import asyncio
import os
import uuid

import config
from db import init_db
from llm import describe_api_error
from workflow import Session, apply_edit, create_first_dockerfile


async def main() -> None:
    if not os.path.isdir(config.WEB_APP_DIR):
        print(f"❌ المجلد {config.WEB_APP_DIR} مش موجود. غيّر WEB_APP_DIR في .env أو حط المشروع هناك.")
        return
    if not config.GOOGLE_API_KEY:
        print("❌ GOOGLE_API_KEY مش موجود في ملف .env")
        return

    init_db()
    session = Session(user_id=str(uuid.uuid4()))
    print(f"Session: {session.user_id}")
    print(f"Model: {config.MODEL_NAME} | min interval: {config.MIN_REQUEST_INTERVAL}s")

    try:
        if not await create_first_dockerfile(session):
            return
    except Exception as e:  # noqa: BLE001
        print(describe_api_error(e))
        return

    print(f"\nالـ Dockerfile الحالي:\n\n{session.dockerfile}\n")
    while True:
        request = input("You (اكتب طلب تعديل، أو exit للخروج): ").strip()
        if request.lower() in ("exit", "quit"):
            break
        if not request:
            continue
        try:
            if await apply_edit(session, request):
                print(f"\n[تم تحديث الملف]\n\n{session.dockerfile}\n")
        except Exception as e:  # noqa: BLE001
            print(describe_api_error(e))


if __name__ == "__main__":
    asyncio.run(main())
