import os
import random
import sqlite3
import threading
import time
from datetime import datetime
from html import escape

import requests
from flask import Flask, request


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
DB_PATH = os.environ.get("DB_PATH", "bot.sqlite3")

app = Flask(__name__)
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


def telegram(method, payload):
    if not BOT_TOKEN:
        return None
    return requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=10,
    )


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
        for _ in range(20):
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
    lines.append("Это тестовые аккаунты для dry-run, не реальные Instagram-профили.")
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
        "1. Нажми <b>Аккаунты</b> - увидеть тестовые аккаунты.\n"
        "2. Нажми <b>Запустить</b> - начать безопасный dry-run.\n"
        "3. Нажми <b>Статистика</b> - посмотреть результат.\n"
        "4. Нажми <b>Стоп</b> - остановить.\n\n"
        "Команды: /start, /accounts, /run, /status, /stop, /help"
    )


def background_run():
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
            time.sleep(1)
        state["last"] = "Остановлено." if state["stop"] else f"Готово. Обработано: {done}."
    except Exception as error:
        state["last"] = f"Ошибка: {error}"
    finally:
        conn.close()
        state["running"] = False
        state["stop"] = False


def start_run():
    with lock:
        if state["running"]:
            return "Тест уже идет. Нажми Статистика."
        state["running"] = True
        state["stop"] = False
    threading.Thread(target=background_run, daemon=True).start()
    return "Запустил безопасный dry-run. Через пару секунд нажми Статистика."


def handle_message(chat_id, text):
    text = (text or "").strip().lower()
    if text in {"/start", "start", "старт"}:
        send(chat_id, "Привет. Я простой бот управления.\n\nНажми кнопку ниже.")
    elif text in {"/accounts", "accounts", "аккаунты"}:
        send(chat_id, accounts_text())
    elif text in {"/run", "run", "запустить"}:
        send(chat_id, start_run())
    elif text in {"/status", "status", "статус"}:
        send(chat_id, status_text())
    elif text in {"/stop", "stop", "стоп"}:
        state["stop"] = True
        send(chat_id, "Остановку запросил.")
    elif text in {"/help", "help", "помощь"}:
        send(chat_id, help_text())
    else:
        send(chat_id, "Не понял. Нажми кнопку или напиши /help.")


@app.get("/")
def index():
    token_status = "задан" if BOT_TOKEN else "НЕ ЗАДАН"
    public_url = PUBLIC_URL or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if public_url and not public_url.startswith("http"):
        public_url = "https://" + public_url
    return f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Telegram Bot</title>
      <style>
        body {{ font-family: Arial, sans-serif; background: #f3f4f6; margin: 0; }}
        main {{ max-width: 760px; margin: 0 auto; padding: 32px 18px; }}
        section {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 22px; }}
        code {{ background: #eef2ff; padding: 3px 6px; border-radius: 5px; }}
        a {{ display: inline-block; margin-top: 12px; background: #2563eb; color: white; padding: 11px 15px; border-radius: 7px; text-decoration: none; font-weight: 700; }}
        li {{ margin: 8px 0; }}
      </style>
    </head>
    <body>
      <main>
        <section>
          <h1>Бот запущен</h1>
          <p>Если ты видишь эту страницу, Railway работает.</p>
          <p><b>TELEGRAM_BOT_TOKEN:</b> {escape(token_status)}</p>
          <p><b>PUBLIC_URL:</b> {escape(public_url or "не задан")}</p>
          <ol>
            <li>В Railway Variables должен быть <code>TELEGRAM_BOT_TOKEN</code>.</li>
            <li>В Railway Variables должен быть <code>PUBLIC_URL</code>.</li>
            <li>Нажми кнопку ниже.</li>
            <li>Потом открой Telegram-бота и напиши <code>/start</code>.</li>
          </ol>
          <a href="/setup">Подключить Telegram</a>
        </section>
      </main>
    </body>
    </html>
    """


@app.get("/health")
def health():
    return {"ok": True, "message": "app is alive"}, 200


@app.get("/setup")
def setup():
    if not BOT_TOKEN:
        return "Set TELEGRAM_BOT_TOKEN in Railway Variables.", 400
    public_url = PUBLIC_URL or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if public_url and not public_url.startswith("http"):
        public_url = "https://" + public_url
    if not public_url:
        return "Set PUBLIC_URL in Railway Variables.", 400
    webhook_url = f"{public_url.rstrip('/')}/webhook/{BOT_TOKEN}"
    response = telegram("setWebhook", {"url": webhook_url})
    return response.text if response else "No token", 200


@app.post("/webhook/<token>")
def webhook(token):
    if token != BOT_TOKEN:
        return "forbidden", 403
    update = request.get_json(force=True, silent=True) or {}
    if "message" in update:
        message = update["message"]
        handle_message(message["chat"]["id"], message.get("text", ""))
    elif "callback_query" in update:
        callback = update["callback_query"]
        telegram("answerCallbackQuery", {"callback_query_id": callback["id"]})
        handle_message(callback["message"]["chat"]["id"], callback.get("data", ""))
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
