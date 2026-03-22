"""
Генератор сессии Telegram для GitHub Secrets.
Запускать ЛОКАЛЬНО, не в GitHub Actions.

Результат (строку сессии) добавить как секрет TG_SESSION в настройках репозитория.

Запуск:
    pip install telethon
    python scripts/gen_session.py
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    print("=" * 50)
    print("Telegram Session Generator")
    print("Добавь TG_API_ID и TG_API_HASH на https://my.telegram.org")
    print("=" * 50)

    api_id   = int(input("API ID: ").strip())
    api_hash = input("API Hash: ").strip()

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        await client.start()  # попросит номер телефона и код
        session_string = client.session.save()

    print("\n" + "=" * 50)
    print("✅ Сессия создана! Скопируй строку ниже и добавь как GitHub Secret TG_SESSION:")
    print("=" * 50)
    print(session_string)
    print("=" * 50)
    print("\nGitHub → Settings → Secrets and variables → Actions → New repository secret")
    print("  Name:  TG_SESSION")
    print("  Value: (вставь строку выше)")


if __name__ == "__main__":
    asyncio.run(main())
