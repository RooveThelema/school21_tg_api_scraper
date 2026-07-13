"""
Поллинг статусов проектов School21 и уведомление в Telegram при их изменении.

Настройки — в .env (см. .env.example). Запуск:
    python3 main.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from school21_client import School21Client, School21Error
from telegram_notifier import TelegramNotifier

load_dotenv()

S21_LOGIN = os.environ["S21_LOGIN"]
S21_PASSWORD = os.environ["S21_PASSWORD"]
S21_TRACK_LOGIN = os.environ.get("S21_TRACK_LOGIN", S21_LOGIN)

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

STATUS_LABELS = {
    "ASSIGNED": "назначен",
    "REGISTERED": "зарегистрирован",
    "IN_PROGRESS": "в процессе",
    "IN_REVIEWS": "🔍 нашлась проверка!",
    "ACCEPTED": "✅ принят",
    "FAILED": "❌ не принят",
}


def fetch_statuses(client: School21Client) -> dict[int, dict]:
    projects: dict[int, dict] = {}
    offset = 0
    limit = 50
    while True:
        page = client.participant_projects(S21_TRACK_LOGIN, limit=limit, offset=offset)
        items = page.get("projects") or page.get("items") or page.get("content") or []
        if not items:
            break
        for item in items:
            project_id = item.get("id") or item.get("project_id")
            projects[project_id] = item
        if len(items) < limit:
            break
        offset += limit
    return projects


def main() -> None:
    client = School21Client(S21_LOGIN, S21_PASSWORD)
    notifier = TelegramNotifier(TG_BOT_TOKEN, TG_CHAT_ID)

    print(f"Слежу за проектами {S21_TRACK_LOGIN}, опрос каждые {POLL_INTERVAL_SECONDS}с")

    known_statuses: dict[int, str] = {}
    first_run = True

    while True:
        try:
            projects = fetch_statuses(client)
        except School21Error as exc:
            print(f"Ошибка API: {exc}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for project_id, item in projects.items():
            status = item.get("status")
            name = item.get("name") or item.get("title") or str(project_id)
            previous = known_statuses.get(project_id)

            if previous != status:
                known_statuses[project_id] = status
                if first_run:
                    continue  # не спамим уведомлениями при первом запуске

                label = STATUS_LABELS.get(status, status)
                text = f"{name}: {label}"
                print(text)
                notifier.send(text)

        first_run = False
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
