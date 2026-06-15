#!/usr/bin/env python3
"""
Web panel for Safe Outreach UI Bot.

Deploy target: Railway, Render, Fly.io, or any Python web host.
Default mode is dry-run, so the app can be tested without a browser or a real UI.
"""

from __future__ import annotations

import os
import threading
import time
from html import escape

import requests
from flask import Flask, redirect, render_template_string, request, url_for

from safe_outreach_bot import BotConfig, LeadCandidate, Storage, generate_demo_leads


DB_PATH = os.environ.get("DB_PATH", "safe_outreach.sqlite3")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
APP_TITLE = "Safe Outreach UI Bot"

app = Flask(__name__)
runner_lock = threading.Lock()
runner_state = {
    "running": False,
    "stop": False,
    "last_message": "Готов к запуску.",
}

default_telegram_config = BotConfig(
    db_path=DB_PATH,
    dry_run=True,
    daily_limit=20,
    pause_min_seconds=1,
    pause_max_seconds=2,
    niche_keywords=["bags", "clothes", "jewelry"],
)


PAGE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #1f2937; }
    main { max-width: 960px; margin: 0 auto; padding: 32px 18px; }
    h1 { font-size: 28px; margin: 0 0 6px; }
    p { line-height: 1.5; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 20px 0; }
    .stat, form, table { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; }
    .stat { padding: 16px; }
    .stat strong { display: block; font-size: 26px; margin-top: 6px; }
    form { padding: 18px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    label { font-size: 13px; font-weight: 700; }
    input, select, textarea { width: 100%; box-sizing: border-box; margin-top: 6px; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font: inherit; }
    textarea { min-height: 78px; resize: vertical; }
    .wide { grid-column: 1 / -1; }
    .actions { display: flex; gap: 10px; align-items: center; }
    button, a.button { border: 0; border-radius: 6px; padding: 11px 15px; background: #2563eb; color: white; font-weight: 700; cursor: pointer; text-decoration: none; display: inline-block; }
    button.stop { background: #b91c1c; }
    table { width: 100%; border-collapse: collapse; overflow: hidden; margin-top: 18px; }
    th, td { padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: left; font-size: 14px; }
    th { background: #f9fafb; }
    .note { background: #ecfeff; border: 1px solid #a5f3fc; padding: 12px; border-radius: 8px; }
    @media (max-width: 760px) { .grid, form { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<main>
  <h1>{{ title }}</h1>
  <p class="note">Это безопасная веб-панель для dry-run тестов и вашего staging UI. Реальный Instagram не используется.</p>

  <div class="grid">
    <div class="stat">Новые <strong>{{ stats.new }}</strong></div>
    <div class="stat">Отправлено <strong>{{ stats.sent }}</strong></div>
    <div class="stat">Отказ <strong>{{ stats.rejected }}</strong></div>
    <div class="stat">События <strong>{{ stats.events }}</strong></div>
  </div>

  <p><b>Статус:</b> {{ state.last_message }} {% if state.running %}<b>Идет запуск...</b>{% endif %}</p>

  <form method="post" action="{{ url_for('start') }}">
    <div>
      <label>URL тестового стенда
        <input name="target_url" value="http://localhost:3000">
      </label>
    </div>
    <div>
      <label>Режим
        <select name="dry_run">
          <option value="1" selected>Dry-run</option>
          <option value="0">Staging UI</option>
        </select>
      </label>
    </div>
    <div>
      <label>Ниши через запятую
        <input name="niche_keywords" value="bags, clothes, jewelry">
      </label>
    </div>
    <div>
      <label>Дневной лимит
        <input name="daily_limit" type="number" min="1" max="200" value="20">
      </label>
    </div>
    <div>
      <label>Подписчиков от
        <input name="followers_min" type="number" min="0" value="100">
      </label>
    </div>
    <div>
      <label>Подписчиков до
        <input name="followers_max" type="number" min="0" value="50000">
      </label>
    </div>
    <div>
      <label>Сайт в профиле
        <select name="site_filter">
          <option value="any">Неважно</option>
          <option value="yes">Да</option>
          <option value="no">Нет</option>
        </select>
      </label>
    </div>
    <div>
      <label>Пауза, сек
        <input name="pause" value="1-2">
      </label>
    </div>
    <div class="wide">
      <label>Шаблон сообщения
        <textarea name="message_template">Здравствуйте, {{ "{{username}}" }}! Это тестовое сообщение стенда.</textarea>
      </label>
    </div>
    <div class="wide actions">
      <button type="submit">Запустить</button>
      <a class="button" href="{{ url_for('index') }}">Обновить</a>
    </div>
  </form>

  <form method="post" action="{{ url_for('stop') }}" style="margin-top:12px; display:block;">
    <button class="stop" type="submit">Остановить</button>
  </form>

  <table>
    <thead><tr><th>Профиль</th><th>Ниша</th><th>Подписчики</th><th>Сайт</th><th>Статус</th><th>Когда</th></tr></thead>
    <tbody>
    {% for lead in leads %}
      <tr>
        <td>@{{ lead.username }}</td>
        <td>{{ lead.niche }}</td>
        <td>{{ lead.followers }}</td>
        <td>{{ "да" if lead.has_site else "нет" }}</td>
        <td>{{ lead.status }}</td>
        <td>{{ lead.last_contacted or lead.created_at }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</main>
</body>
</html>
"""


def storage() -> Storage:
    return Storage(DB_PATH)


def recent_leads(limit: int = 30):
    db = storage()
    try:
        rows = db.conn.execute(
            """
            SELECT username, niche, followers, has_site, status, last_contacted, created_at
            FROM leads
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


@app.get("/")
def index():
    db = storage()
    try:
        stats = db.stats()
    finally:
        db.close()
    return render_template_string(
        PAGE,
        title=APP_TITLE,
        stats=stats,
        state=runner_state,
        leads=recent_leads(),
    )


@app.post("/start")
def start():
    with runner_lock:
        if runner_state["running"]:
            runner_state["last_message"] = "Тест уже идет."
            return redirect(url_for("index"))
        runner_state["running"] = True
        runner_state["stop"] = False

    config = config_from_form(request.form)
    thread = threading.Thread(target=run_background, args=(config,), daemon=True)
    thread.start()
    return redirect(url_for("index"))


@app.post("/stop")
def stop():
    runner_state["stop"] = True
    runner_state["last_message"] = "Остановка запрошена."
    return redirect(url_for("index"))


@app.get("/telegram/setup")
def telegram_setup():
    if not TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN не задан в Railway Variables.", 400
    public_url = get_public_url()
    if not public_url:
        return "PUBLIC_URL не задан. Укажи ссылку Railway в Variables.", 400
    webhook_url = f"{public_url.rstrip('/')}/telegram/{TELEGRAM_BOT_TOKEN}"
    response = telegram_api("setWebhook", {"url": webhook_url})
    return response.text, response.status_code


@app.post("/telegram/<token>")
def telegram_webhook(token: str):
    if token != TELEGRAM_BOT_TOKEN:
        return "forbidden", 403

    update = request.get_json(force=True, silent=True) or {}
    if "message" in update:
        handle_telegram_message(update["message"])
    elif "callback_query" in update:
        handle_telegram_callback(update["callback_query"])
    return "ok"


def config_from_form(form) -> BotConfig:
    pause_min, pause_max = parse_pause(form.get("pause", "1-2"))
    keywords = [
        item.strip()
        for item in form.get("niche_keywords", "bags, clothes, jewelry").split(",")
        if item.strip()
    ]
    return BotConfig(
        target_url=form.get("target_url", "http://localhost:3000"),
        db_path=DB_PATH,
        dry_run=form.get("dry_run", "1") == "1",
        daily_limit=int(form.get("daily_limit", 20)),
        pause_min_seconds=pause_min,
        pause_max_seconds=pause_max,
        niche_keywords=keywords or ["bags"],
        followers_min=int(form.get("followers_min", 100)),
        followers_max=int(form.get("followers_max", 50000)),
        site_filter=form.get("site_filter", "any"),
        message_template=form.get("message_template", "Здравствуйте, {{username}}!"),
    )


def get_public_url() -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if railway_domain:
        return f"https://{railway_domain}"
    return ""


def telegram_api(method: str, payload: dict) -> requests.Response:
    return requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        json=payload,
        timeout=10,
    )


def telegram_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "👥 Аккаунты", "callback_data": "accounts"},
                {"text": "🚀 Запустить", "callback_data": "run"},
            ],
            [
                {"text": "📊 Статистика", "callback_data": "status"},
                {"text": "⏹ Стоп", "callback_data": "stop"},
            ],
            [{"text": "❓ Помощь", "callback_data": "help"}],
        ]
    }


def send_telegram(chat_id: int, text: str, keyboard: bool = True) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = telegram_keyboard()
    telegram_api("sendMessage", payload)


def answer_callback(callback_id: str) -> None:
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})


def handle_telegram_message(message: dict) -> None:
    chat_id = int(message["chat"]["id"])
    text = (message.get("text") or "").strip().lower()

    if text in {"/start", "start", "старт"}:
        send_telegram(
            chat_id,
            "Привет. Я панель управления тестовым ботом.\n\n"
            "Нажми <b>Аккаунты</b>, и я сразу покажу тестовые аккаунты.\n"
            "Нажми <b>Запустить</b>, чтобы начать dry-run тест.\n\n"
            "Все безопасно: реальные аккаунты Instagram не используются.",
        )
    elif text in {"/accounts", "аккаунты", "accounts"}:
        send_telegram(chat_id, build_accounts_text())
    elif text in {"/status", "статус", "status"}:
        send_telegram(chat_id, build_status_text())
    elif text in {"/run", "запустить", "run"}:
        send_telegram(chat_id, start_telegram_run())
    elif text in {"/stop", "стоп", "stop"}:
        runner_state["stop"] = True
        send_telegram(chat_id, "Остановку запросил. Если тест идет, он аккуратно завершится.")
    elif text in {"/help", "помощь", "help"}:
        send_telegram(chat_id, build_help_text())
    else:
        send_telegram(
            chat_id,
            "Я понимаю простые команды:\n\n"
            "/start - меню\n"
            "/accounts - дать тестовые аккаунты\n"
            "/run - запустить dry-run\n"
            "/status - статистика\n"
            "/stop - остановить\n"
            "/help - помощь",
        )


def handle_telegram_callback(callback: dict) -> None:
    answer_callback(callback["id"])
    chat_id = int(callback["message"]["chat"]["id"])
    action = callback.get("data", "")

    if action == "accounts":
        send_telegram(chat_id, build_accounts_text())
    elif action == "run":
        send_telegram(chat_id, start_telegram_run())
    elif action == "status":
        send_telegram(chat_id, build_status_text())
    elif action == "stop":
        runner_state["stop"] = True
        send_telegram(chat_id, "Остановку запросил. Если тест идет, он аккуратно завершится.")
    elif action == "help":
        send_telegram(chat_id, build_help_text())


def build_accounts_text() -> str:
    db = storage()
    try:
        leads = recent_leads(10)
        if not leads:
            demo_leads = generate_demo_leads(default_telegram_config)[:10]
            for lead in demo_leads:
                db.upsert_lead(lead)
            leads = recent_leads(10)
    finally:
        db.close()

    lines = ["<b>Тестовые аккаунты</b>", ""]
    for index, lead in enumerate(leads, start=1):
        site = "сайт: да" if lead["has_site"] else "сайт: нет"
        lines.append(
            f"{index}. @{escape(str(lead['username']))} | "
            f"{escape(str(lead['niche']))} | "
            f"{lead['followers']} подписчиков | {site} | {escape(str(lead['status']))}"
        )
    lines.append("")
    lines.append("Это демо-лиды для тестового стенда, не реальные Instagram-аккаунты.")
    return "\n".join(lines)


def build_status_text() -> str:
    db = storage()
    try:
        stats = db.stats()
    finally:
        db.close()

    running = "да" if runner_state["running"] else "нет"
    return (
        "<b>Статистика</b>\n\n"
        f"Новые: {stats['new']}\n"
        f"Отправлено: {stats['sent']}\n"
        f"Отказ: {stats['rejected']}\n"
        f"События: {stats['events']}\n"
        f"Тест идет: {running}\n\n"
        f"Последнее: {escape(str(runner_state['last_message']))}"
    )


def build_help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "1. Нажми <b>Аккаунты</b>, чтобы увидеть тестовые аккаунты.\n"
        "2. Нажми <b>Запустить</b>, чтобы начать безопасный dry-run.\n"
        "3. Нажми <b>Статистика</b>, чтобы проверить результат.\n"
        "4. Нажми <b>Стоп</b>, если нужно остановить.\n\n"
        "Команды текстом:\n"
        "/accounts\n/run\n/status\n/stop\n/help"
    )


def start_telegram_run() -> str:
    with runner_lock:
        if runner_state["running"]:
            return "Тест уже идет. Нажми Статистика, чтобы посмотреть прогресс."
        runner_state["running"] = True
        runner_state["stop"] = False

    thread = threading.Thread(
        target=run_background,
        args=(default_telegram_config,),
        daemon=True,
    )
    thread.start()
    return "Запустил безопасный dry-run тест. Через пару секунд нажми Статистика."


def parse_pause(value: str) -> tuple[int, int]:
    parts = value.replace(" ", "").split("-", 1)
    if len(parts) == 1:
        seconds = max(0, int(parts[0]))
        return seconds, seconds
    first = max(0, int(parts[0]))
    second = max(first, int(parts[1]))
    return first, second


def run_background(config: BotConfig) -> None:
    db = storage()
    try:
        runner_state["last_message"] = "Готовлю тестовых лидов."
        leads = generate_demo_leads(config)
        sent = 0

        for lead in leads:
            if runner_state["stop"]:
                break
            if db.daily_sent_count() >= config.daily_limit:
                runner_state["last_message"] = "Дневной лимит достигнут."
                break
            if db.already_finished(lead.username):
                continue

            db.upsert_lead(lead)
            message = config.message_template.replace("{{username}}", lead.username)
            db.add_event(lead.username, "open_profile", "web dry-run")
            db.add_event(lead.username, "type_message", f"{len(message)} chars")
            db.add_event(lead.username, "submit_message", "web dry-run")
            db.mark_sent(lead.username, message)
            sent += 1
            runner_state["last_message"] = f"Dry-run отправка {sent}/{config.daily_limit}: @{escape(lead.username)}"
            time.sleep(config.pause_min_seconds)

        if runner_state["stop"]:
            runner_state["last_message"] = "Тест остановлен."
        else:
            runner_state["last_message"] = f"Готово. Отправок dry-run: {sent}."
    except Exception as exc:
        runner_state["last_message"] = f"Ошибка: {exc}"
    finally:
        db.close()
        runner_state["running"] = False
        runner_state["stop"] = False


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    if TELEGRAM_BOT_TOKEN and get_public_url():
        webhook = f"{get_public_url().rstrip('/')}/telegram/{TELEGRAM_BOT_TOKEN}"
        try:
            telegram_api("setWebhook", {"url": webhook})
        except Exception:
            pass
    app.run(host="0.0.0.0", port=port)
