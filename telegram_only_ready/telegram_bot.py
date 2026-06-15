import os
import random
import sqlite3
import threading
import time
from datetime import datetime
from html import escape

import requests


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", "bot.sqlite3")
BOT_VERSION = "telegram-only-v2"

state = {"running": False, "stop": False, "last": "Готов к работе."}
lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            username TEXT PRIMARY KEY,
            niche TEXT NOT NULL,
            followers INTEGER NOT NULL,
            has_site INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def telegram(method, payload=None):
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "Аккаунты", "callback_data": "accounts"},
                {"text": "Запустить", "callback_data": "run"},
            ],
            [
                {"text": "Статистика", "callback_data": "status"},
                {"text": "Стоп", "callback_data": "stop"},
            ],
            [{"text": "Помощь", "callback_data": "help"}],
        ]
    }


def send(chat_id, text):
    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard(),
            "disable_web_page_preview": True,
        },
    )


def ensure_accounts():
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"]
        if count:
            return
        niches = ["bags", "clothes", "jewelry", "beauty", "fitness"]
        for _ in range(30):
            niche = random.choice(niches)
            username = f"test_{niche}_{random.randint(1000, 9999)}"
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts
                (username, niche, followers, has_site, status, created_at)
                VALUES (?, ?, ?, ?, 'new', ?)
                """,
                (
                    username,
                    niche,
                    random.randint(300, 50000),
                    random.choice([0, 1]),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def reset_accounts():
    conn = get_db()
    try:
        conn.execute("DELETE FROM accounts")
        conn.commit()
    finally:
        conn.close()
    ensure_accounts()


def accounts_text():
    ensure_accounts()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT username, niche, followers, has_site, status
            FROM accounts
            ORDER BY created_at DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        conn.close()

    lines = ["<b>Тестовые аккаунты</b>", ""]
    for index, row in enumerate(rows, 1):
        site = "сайт: да" if row["has_site"] else "сайт: нет"
        lines.append(
            f"{index}. @{escape(row['username'])} | {escape(row['niche'])} | "
            f"{row['followers']} подписчиков | {site} | {escape(row['status'])}"
        )
    lines.append("")
    lines.append("Это тестовые dry-run аккаунты, не реальные Instagram-профили.")
    return "\n".join(lines)


def status_text():
    ensure_accounts()
    conn = get_db()
    try:
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM accounts GROUP BY status").fetchall()
    finally:
        conn.close()

    stats = {"new": 0, "sent": 0, "rejected": 0}
    for row in rows:
        stats[row["status"]] = row["count"]
    running = "да" if state["running"] else "нет"
    return (
        "<b>Статистика</b>\n\n"
        f"Новые: {stats['new']}\n"
        f"Обработано: {stats['sent']}\n"
        f"Отказ: {stats['rejected']}\n"
        f"Тест идет: {running}\n\n"
        f"Последнее: {escape(state['last'])}"
    )


def help_text():
    return (
        "<b>Как пользоваться</b>\n\n"
        "Жми кнопки под сообщением:\n\n"
        "<b>Аккаунты</b> - показать тестовые аккаунты.\n"
        "<b>Запустить</b> - начать безопасный dry-run.\n"
        "<b>Статистика</b> - посмотреть результат.\n"
        "<b>Стоп</b> - остановить.\n\n"
        "Команды текстом:\n"
        "/start\n/accounts\n/run\n/status\n/stop\n/reset\n/help"
    )


def version_text():
    return f"Версия бота: {BOT_VERSION}"


def background_run(chat_id):
    ensure_accounts()
    conn = get_db()
    try:
        rows = conn.execute("SELECT username FROM accounts WHERE status = 'new' LIMIT 20").fetchall()
        done = 0
        for row in rows:
            if state["stop"]:
                break
            username = row["username"]
            message = f"Здравствуйте, {username}! Это тестовое сообщение."
            conn.execute(
                """
                UPDATE accounts
                SET status = 'sent', message = ?, updated_at = ?
                WHERE username = ?
                """,
                (message, datetime.now().isoformat(timespec="seconds"), username),
            )
            conn.commit()
            done += 1
            state["last"] = f"Dry-run обработал @{username} ({done}/20)"
            if done in {1, 5, 10, 20}:
                send(chat_id, state["last"])
            time.sleep(1)
        state["last"] = "Остановлено." if state["stop"] else f"Готово. Обработано: {done}."
        send(chat_id, state["last"])
    except Exception as error:
        state["last"] = f"Ошибка: {error}"
        send(chat_id, state["last"])
    finally:
        conn.close()
        state["running"] = False
        state["stop"] = False


def start_run(chat_id):
    with lock:
        if state["running"]:
            return "Тест уже идет. Нажми Статистика."
        state["running"] = True
        state["stop"] = False
    threading.Thread(target=background_run, args=(chat_id,), daemon=True).start()
    return "Запустил безопасный dry-run. Я сам напишу прогресс."


def handle(chat_id, text):
    text = (text or "").strip().lower()
    if text in {"/start", "start", "старт"}:
        send(chat_id, f"Привет. Я простой Telegram-бот.\n\nВерсия: {BOT_VERSION}\n\nНажми кнопку ниже.")
    elif text in {"/accounts", "accounts", "аккаунты"}:
        send(chat_id, accounts_text())
    elif text in {"/run", "run", "запустить"}:
        send(chat_id, start_run(chat_id))
    elif text in {"/status", "status", "статус"}:
        send(chat_id, status_text())
    elif text in {"/stop", "stop", "стоп"}:
        state["stop"] = True
        send(chat_id, "Остановку запросил.")
    elif text in {"/reset", "reset", "сброс"}:
        reset_accounts()
        send(chat_id, "Сбросил базу и создал новые тестовые аккаунты.")
    elif text in {"/help", "help", "помощь"}:
        send(chat_id, help_text())
    elif text in {"/version", "version", "версия"}:
        send(chat_id, version_text())
    else:
        send(chat_id, "Не понял. Нажми кнопку или напиши /help.")


def handle_update(update):
    if "message" in update:
        message = update["message"]
        handle(message["chat"]["id"], message.get("text", ""))
    elif "callback_query" in update:
        callback = update["callback_query"]
        telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
        handle(callback["message"]["chat"]["id"], callback.get("data", ""))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Add TELEGRAM_BOT_TOKEN in Railway Variables")
    ensure_accounts()
    telegram("deleteWebhook", {"drop_pending_updates": True})
    telegram(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Открыть меню"},
                {"command": "accounts", "description": "Показать тестовые аккаунты"},
                {"command": "run", "description": "Запустить dry-run"},
                {"command": "status", "description": "Статистика"},
                {"command": "stop", "description": "Остановить"},
                {"command": "reset", "description": "Новые тестовые аккаунты"},
                {"command": "version", "description": "Версия бота"},
                {"command": "help", "description": "Помощь"},
            ]
        },
    )
    print("Bot started in polling mode", flush=True)

    offset = None
    while True:
        try:
            payload = {"timeout": 25}
            if offset is not None:
                payload["offset"] = offset
            updates = telegram("getUpdates", payload).get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                handle_update(update)
        except Exception as error:
            print(f"Polling error: {error}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
