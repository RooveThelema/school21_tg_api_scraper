"""
Telegram-бот для School21 API.

- /start показывает меню с инлайн-кнопками; при навигации меню НЕ удаляется,
  а редактируется (editMessageText) — можно ходить туда-сюда.
- Действия (запросы к API) отправляются ОТДЕЛЬНЫМИ сообщениями,
  у каждого такого сообщения есть кнопка 🗑 для его удаления.

Запуск: ./venv/bin/python bot.py
"""

from __future__ import annotations

import html
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from school21_client import School21Client, School21Error

load_dotenv()

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = int(os.environ["TG_CHAT_ID"])
S21_LOGIN = os.environ["S21_LOGIN"]
S21_PASSWORD = os.environ["S21_PASSWORD"]
ME = os.environ.get("S21_TRACK_LOGIN", S21_LOGIN)

TG_API = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

client = School21Client(S21_LOGIN, S21_PASSWORD)


# ---------- Telegram helpers ----------

def tg(method: str, **kwargs) -> dict:
    response = requests.post(f"{TG_API}/{method}", json=kwargs, timeout=40)
    return response.json()


def send(text: str, keyboard: list | None = None) -> None:
    tg(
        "sendMessage",
        chat_id=TG_CHAT_ID,
        text=text,
        parse_mode="HTML",
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def edit(message_id: int, text: str, keyboard: list) -> None:
    result = tg(
        "editMessageText",
        chat_id=TG_CHAT_ID,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        reply_markup={"inline_keyboard": keyboard},
    )
    # "message is not modified" — не ошибка, просто нажали ту же кнопку
    if not result.get("ok") and "not modified" not in result.get("description", ""):
        print("editMessageText:", result.get("description"))


def answer(callback_id: str, text: str = "") -> None:
    tg("answerCallbackQuery", callback_query_id=callback_id, text=text)


DELETE_BUTTON = [[{"text": "🗑 Удалить", "callback_data": "del"}]]


def send_result(title: str, body: str, extra_buttons: list | None = None) -> None:
    keyboard = (extra_buttons or []) + DELETE_BUTTON
    send(f"<b>{html.escape(title)}</b>\n{body}", keyboard)


def fmt_json(data) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > 3400:
        text = text[:3400] + "\n… (обрезано)"
    return f"<pre>{html.escape(text)}</pre>"


# ---------- Меню (только editMessageText, никогда не удаляется) ----------

def kb(rows: list[list[tuple[str, str]]]) -> list:
    return [[{"text": t, "callback_data": d} for t, d in row] for row in rows]


MAIN_MENU = (
    f"🏠 <b>School21 — {ME}</b>\nВыбирай раздел:",
    kb([
        [("👤 Профиль", "m:profile"), ("📁 Проекты", "m:projects")],
        [("🎓 Курсы", "a:courses"), ("📅 События (14 дней)", "a:events")],
        [("🏫 Кампусы", "m:campus"), ("💸 Продажи", "a:sales")],
        [("🗺 Граф проектов", "a:graph")],
        [("🔍 ПИНГ: проверки (важно!)", "a:ping_reviews")],
        [("🧘 ПИНГ: события кампуса", "a:ping_events")],
    ]),
)

PROFILE_MENU = (
    "👤 <b>Профиль</b> — что глянуть:",
    kb([
        [("ℹ️ Инфо", "a:info"), ("🏅 Поинты (PRP/CRP)", "a:points")],
        [("🧠 Скиллы", "a:skills"), ("⏱ Логтайм", "a:logtime")],
        [("⭐ Фидбек", "a:feedback"), ("📈 История XP", "a:xp")],
        [("🎖 Бейджи", "a:badges"), ("🛡 Коалиция", "a:coalition")],
        [("💻 Рабочее место", "a:workstation")],
        [("⬅️ Назад", "m:main")],
    ]),
)

PROJECTS_MENU = (
    "📁 <b>Проекты</b> — фильтр по статусу:",
    kb([
        [("🔍 На проверке", "a:proj:IN_REVIEWS")],
        [("🚧 В процессе", "a:proj:IN_PROGRESS"), ("📝 Зарегистрирован", "a:proj:REGISTERED")],
        [("📌 Назначен", "a:proj:ASSIGNED")],
        [("✅ Приняты", "a:proj:ACCEPTED"), ("❌ Провалены", "a:proj:FAILED")],
        [("📋 Все проекты", "a:proj:ALL")],
        [("⬅️ Назад", "m:main")],
    ]),
)


def campus_menu() -> tuple[str, list]:
    campuses = client.get("/campuses").get("campuses", [])
    rows = [[(f"🏫 {c.get('shortName') or c.get('fullName')}", f"c:{c['id']}")] for c in campuses[:20]]
    rows.append([("⬅️ Назад", "m:main")])
    return "🏫 <b>Кампусы</b> — выбери:", kb(rows)


def campus_detail_menu(campus_id: str) -> tuple[str, list]:
    return (
        "🏫 <b>Кампус</b> — что глянуть:",
        kb([
            [("👥 Участники", f"a:cp:{campus_id}")],
            [("🛡 Коалиции", f"a:cc:{campus_id}"), ("🖥 Кластеры", f"a:cl:{campus_id}")],
            [("⬅️ К кампусам", "m:campus"), ("🏠 Меню", "m:main")],
        ]),
    )


# ---------- Действия (отдельные сообщения с кнопкой удаления) ----------

def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def action_ping_reviews() -> None:
    """Проверки: кричим только про НОВЫЕ IN_REVIEWS с прошлого пинга."""
    reviews = client.get(f"/participants/{ME}/projects", {"status": "IN_REVIEWS", "limit": 50})
    items = reviews.get("projects") or []

    state = load_state()
    known: dict = state.get("in_reviews", {})
    today = datetime.now(timezone.utc).strftime("%d.%m %H:%M")

    current_ids = set()
    new_lines, old_lines = [], []
    for p in items:
        pid = str(p.get("id"))
        title = html.escape(str(p.get("title") or p.get("name") or pid))
        current_ids.add(pid)
        if pid in known:
            old_lines.append(f"• {title} — в проверках с {known[pid]}")
        else:
            known[pid] = today
            new_lines.append(f"• <b>{title}</b>")

    # выкидываем из памяти то, что из проверок уже ушло
    state["in_reviews"] = {pid: seen for pid, seen in known.items() if pid in current_ids}
    save_state(state)

    parts = []
    if new_lines:
        parts.append("🚨 <b>ПРОВЕРКА НАШЛАСЬ! Новое:</b>\n" + "\n".join(new_lines))
    if old_lines:
        parts.append("⏳ Давно висит в проверках (не ново):\n" + "\n".join(old_lines))
    if not items:
        parts.append("Проектов в проверках нет.")
    elif not new_lines:
        parts.insert(0, "✅ Новых проверок с прошлого пинга нет.")

    send_result("🔍 ПИНГ: проверки", "\n\n".join(parts))


def action_ping_events() -> None:
    """События кампуса (йога, воркшопы, экзамены) на неделю вперёд."""
    now = datetime.now(timezone.utc)
    events = client.get("/events", {"from": iso(now), "to": iso(now + timedelta(days=7)), "limit": 50})
    ev_items = events.get("events") or []
    if not ev_items:
        send_result("🧘 ПИНГ: события кампуса", "Событий на ближайшие 7 дней нет.")
        return

    lines = []
    for e in ev_items[:20]:
        name = html.escape(str(e.get("name") or e.get("type", "событие")))
        location = html.escape(str(e.get("location") or "?"))
        start = str(e.get("startDateTime") or "")[:16].replace("T", " ")
        lines.append(f"• <b>{name}</b>\n  📍 {location} — 🕐 {start}")
    send_result(f"🧘 События кампуса на 7 дней ({len(ev_items)})", "\n".join(lines))


def action_projects(status: str) -> None:
    params = {"limit": 50}
    if status != "ALL":
        params["status"] = status
    data = client.get(f"/participants/{ME}/projects", params)
    items = data.get("projects") or []
    if not items:
        send_result(f"Проекты [{status}]", "Пусто.")
        return
    lines = [
        f"• <b>{html.escape(str(p.get('title') or p.get('name') or p.get('id')))}</b> — {p.get('status', '?')}"
        for p in items[:40]
    ]
    send_result(f"Проекты [{status}] ({len(items)})", "\n".join(lines))


def action_clusters(campus_id: str) -> None:
    data = client.get(f"/campuses/{campus_id}/clusters")
    clusters = data.get("clusters") or []
    lines = [f"• {c.get('name')} (этаж {c.get('floor', '?')}, id {c.get('id')})" for c in clusters[:30]]
    map_buttons = [
        [{"text": f"🗺 Карта: {c.get('name')}", "callback_data": f"a:map:{c['id']}"}]
        for c in clusters[:8]
    ]
    send_result("Кластеры кампуса", "\n".join(lines) or "Пусто.", map_buttons)


SIMPLE_ACTIONS = {
    "info": ("Профиль", lambda: client.get(f"/participants/{ME}")),
    "points": ("Поинты", lambda: client.get(f"/participants/{ME}/points")),
    "skills": ("Скиллы", lambda: client.get(f"/participants/{ME}/skills")),
    "logtime": ("Логтайм (ср. за неделю)", lambda: client.get(f"/participants/{ME}/logtime")),
    "feedback": ("Фидбек", lambda: client.get(f"/participants/{ME}/feedback")),
    "xp": ("История XP", lambda: client.get(f"/participants/{ME}/experience-history", {"limit": 30})),
    "badges": ("Бейджи", lambda: client.get(f"/participants/{ME}/badges")),
    "coalition": ("Коалиция", lambda: client.get(f"/participants/{ME}/coalition")),
    "workstation": ("Рабочее место", lambda: client.get(f"/participants/{ME}/workstation")),
    "courses": ("Курсы", lambda: client.get(f"/participants/{ME}/courses")),
    "sales": ("Продажи", lambda: client.get("/sales")),
    "graph": ("Граф проектов", lambda: client.get("/graph")),
}


def handle_action(action: str) -> None:
    try:
        if action == "ping_reviews":
            action_ping_reviews()
        elif action == "ping_events":
            action_ping_events()
        elif action.startswith("proj:"):
            action_projects(action.split(":", 1)[1])
        elif action.startswith("cp:"):
            data = client.get(f"/campuses/{action[3:]}/participants", {"limit": 50})
            send_result("Участники кампуса", fmt_json(data))
        elif action.startswith("cc:"):
            data = client.get(f"/campuses/{action[3:]}/coalitions")
            send_result("Коалиции кампуса", fmt_json(data))
        elif action.startswith("cl:"):
            action_clusters(action[3:])
        elif action.startswith("map:"):
            data = client.get(f"/clusters/{action[4:]}/map")
            send_result("Карта кластера", fmt_json(data))
        elif action in SIMPLE_ACTIONS:
            title, fn = SIMPLE_ACTIONS[action]
            send_result(title, fmt_json(fn()))
        else:
            send_result("Ошибка", f"Неизвестное действие: {html.escape(action)}")
    except School21Error as exc:
        send_result("Ошибка API", f"<pre>{html.escape(str(exc)[:1000])}</pre>")
    except Exception as exc:  # noqa: BLE001 — бот не должен умирать из-за одной кнопки
        send_result("Ошибка", f"<pre>{html.escape(f'{type(exc).__name__}: {exc}'[:1000])}</pre>")


# ---------- Автослежка (фоновый поток) ----------

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))

STATUS_LABELS = {
    "ASSIGNED": "📌 назначен",
    "REGISTERED": "📝 зарегистрирован",
    "IN_PROGRESS": "🚧 в процессе",
    "IN_REVIEWS": "🚨 УШЁЛ В ПРОВЕРКИ!",
    "ACCEPTED": "✅ ПРИНЯТ",
    "FAILED": "❌ не принят",
}


def watcher() -> None:
    """Каждые POLL_INTERVAL сек: смена статусов проектов + новые события кампуса."""
    first_run = True
    while True:
        try:
            state = load_state()
            statuses: dict = state.get("statuses", {})
            seen_events: list = state.get("seen_events", [])

            # --- статусы проектов ---
            data = client.get(f"/participants/{ME}/projects", {"limit": 100})
            for p in data.get("projects") or []:
                pid = str(p.get("id"))
                status = p.get("status")
                title = html.escape(str(p.get("title") or pid))
                if statuses.get(pid) != status:
                    statuses[pid] = status
                    if not first_run:
                        label = STATUS_LABELS.get(status, status)
                        send_result("Проект изменился", f"<b>{title}</b>: {label}")

            # --- новые события кампуса ---
            now = datetime.now(timezone.utc)
            ev_data = client.get(
                "/events",
                {"from": iso(now), "to": iso(now + timedelta(days=30)), "limit": 50},
            )
            for e in ev_data.get("events") or []:
                eid = e.get("id")
                if eid in seen_events:
                    continue
                seen_events.append(eid)
                if not first_run:
                    name = html.escape(str(e.get("name") or "событие"))
                    etype = html.escape(str(e.get("type") or ""))
                    location = html.escape(str(e.get("location") or "?"))
                    start = str(e.get("startDateTime") or "")[:16].replace("T", " ")
                    capacity = e.get("capacity", "?")
                    registered = e.get("registerCount", "?")
                    send_result(
                        "🆕 Новое событие в кампусе",
                        f"<b>{name}</b> ({etype})\n📍 {location}\n🕐 {start}\n"
                        f"👥 мест: {capacity}, записалось: {registered}",
                    )

            state["statuses"] = statuses
            state["seen_events"] = seen_events[-500:]
            save_state(state)
            first_run = False
        except Exception as exc:  # noqa: BLE001
            print(f"watcher: {type(exc).__name__}: {exc}")
        time.sleep(POLL_INTERVAL)


# ---------- Роутинг ----------

def handle_callback(cq: dict) -> None:
    data = cq.get("data", "")
    message_id = cq["message"]["message_id"]
    answer(cq["id"])

    if data == "del":
        tg("deleteMessage", chat_id=TG_CHAT_ID, message_id=message_id)
        return

    if data.startswith("m:") or data.startswith("c:"):
        # навигация: меню всегда редактируется, никогда не удаляется
        try:
            if data == "m:main":
                text, keyboard = MAIN_MENU
            elif data == "m:profile":
                text, keyboard = PROFILE_MENU
            elif data == "m:projects":
                text, keyboard = PROJECTS_MENU
            elif data == "m:campus":
                text, keyboard = campus_menu()
            elif data.startswith("c:"):
                text, keyboard = campus_detail_menu(data[2:])
            else:
                return
        except School21Error as exc:
            send_result("Ошибка API", f"<pre>{html.escape(str(exc)[:1000])}</pre>")
            return
        edit(message_id, text, keyboard)
        return

    if data.startswith("a:"):
        handle_action(data[2:])


def main() -> None:
    print(f"Бот запущен. Логин: {ME}. Автослежка каждые {POLL_INTERVAL}с. Напиши боту /start")
    threading.Thread(target=watcher, daemon=True).start()
    offset = 0
    while True:
        try:
            updates = tg("getUpdates", offset=offset, timeout=30)
        except requests.RequestException as exc:
            print("getUpdates:", exc)
            continue

        if not updates.get("ok"):
            print("getUpdates error:", updates)
            continue

        for update in updates.get("result", []):
            print("update:", json.dumps(update, ensure_ascii=False)[:200])
            offset = update["update_id"] + 1

            message = update.get("message")
            if message and message["chat"]["id"] == TG_CHAT_ID:
                text, keyboard = MAIN_MENU
                send(text, keyboard)

            cq = update.get("callback_query")
            if cq and cq["message"]["chat"]["id"] == TG_CHAT_ID:
                try:
                    handle_callback(cq)
                except Exception as exc:  # noqa: BLE001
                    print(f"handle_callback: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
