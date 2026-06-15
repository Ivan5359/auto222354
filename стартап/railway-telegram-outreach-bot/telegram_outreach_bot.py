import datetime as dt
import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH = os.environ.get("OUTREACH_BOT_DB", "outreach_bot.sqlite3")
API_BASE = f"https://api.telegram.org/bot{TOKEN}"


DEFAULT_MESSAGE = """Здравствуйте! Увидел ваш бренд и подумал, что вам может быть полезна простая страница/мини-сайт.

Можно собрать в одном месте товары, описание бренда, условия заказа, доставку, контакты и кнопку для покупки.

Сейчас собираю первые кейсы, поэтому могу сделать тестовый концепт по доступной цене. Если интересно, могу показать пример."""

DEFAULT_FOLLOWUP = """Здравствуйте! Аккуратно продублирую сообщение.

Я могу подготовить тестовый концепт сайта под ваш бренд: товары, описание, доставка, контакты и кнопка заказа в одном месте.

Если сейчас неактуально, всё хорошо. Просто хотел уточнить, может ли вам это быть интересно."""

DEFAULT_SETTINGS = {
    "min_followers": "500",
    "max_followers": "5000",
    "site_mode": "no_site",
    "niche": "",
    "daily_limit": "20",
    "followup_hours": "48",
    "message_template": DEFAULT_MESSAGE,
    "followup_template": DEFAULT_FOLLOWUP,
}


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today():
    return dt.date.today().isoformat()


def api(method, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=70) as res:
        body = json.loads(res.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(body)
    return body["result"]


def send(chat_id, text, markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup:
        payload["reply_markup"] = markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return api("sendMessage", payload)


def answer_callback(callback_id, text=""):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    return api("answerCallbackQuery", payload)


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                handle TEXT PRIMARY KEY,
                followers INTEGER NOT NULL DEFAULT 0,
                niche TEXT NOT NULL DEFAULT '',
                has_site INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT '',
                reply_at TEXT NOT NULL DEFAULT '',
                followup_at TEXT NOT NULL DEFAULT '',
                last_message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS user_state (chat_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        for key, value in DEFAULT_SETTINGS.items():
            con.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )


def setting(key):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def set_state(chat_id, state):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO user_state(chat_id, state) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET state = excluded.state",
            (str(chat_id), state),
        )


def get_state(chat_id):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT state FROM user_state WHERE chat_id = ?", (str(chat_id),)).fetchone()
    return row[0] if row else ""


def clear_state(chat_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM user_state WHERE chat_id = ?", (str(chat_id),))


def normalize_handle(value):
    value = value.strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I)
    value = value.split("?")[0].split("/")[0].lstrip("@").lower()
    value = re.sub(r"[^a-z0-9._]", "", value)
    return f"@{value}" if value else ""


def parse_followers(value):
    raw = str(value or "").strip().lower().replace(",", ".")
    multiplier = 1
    if any(mark in raw for mark in ["k", "к", "тыс", "тис"]):
        multiplier = 1000
    if any(mark in raw for mark in ["m", "млн"]):
        multiplier = 1000000
    match = re.search(r"\d+(\.\d+)?", raw)
    return int(float(match.group(0)) * multiplier) if match else 0


def parse_site(value):
    raw = str(value or "").lower()
    if any(word in raw for word in ["no", "нет", "немає", "без", "no-site", "nosite"]):
        return 0
    if any(word in raw for word in ["has", "есть", "є", "site", "сайт", "website"]):
        return 1
    return 0


def parse_lead(line):
    parts = [part.strip() for part in line.split("|")]
    if not parts or not parts[0]:
        return None
    handle = normalize_handle(parts[0])
    if not handle:
        return None
    return {
        "handle": handle,
        "followers": parse_followers(parts[1] if len(parts) > 1 else ""),
        "niche": parts[2] if len(parts) > 2 else "",
        "has_site": parse_site(parts[3] if len(parts) > 3 else ""),
        "note": " | ".join(parts[4:]) if len(parts) > 4 else "",
    }


def add_leads(text):
    added = 0
    skipped = 0
    with sqlite3.connect(DB_PATH) as con:
        existing = {row[0] for row in con.execute("SELECT handle FROM leads")}
        for line in text.splitlines():
            lead = parse_lead(line)
            if not lead or lead["handle"] in existing:
                skipped += 1
                continue
            con.execute(
                """
                INSERT INTO leads(handle, followers, niche, has_site, note, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    lead["handle"],
                    lead["followers"],
                    lead["niche"],
                    lead["has_site"],
                    lead["note"],
                    now(),
                ),
            )
            existing.add(lead["handle"])
            added += 1
    return added, skipped


def leads():
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM leads ORDER BY created_at")]


def get_lead(handle):
    handle = normalize_handle(handle)
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM leads WHERE handle = ?", (handle,)).fetchone()
    return dict(row) if row else None


def update_lead(handle, **values):
    if not values:
        return
    handle = normalize_handle(handle)
    fields = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [handle]
    with sqlite3.connect(DB_PATH) as con:
        con.execute(f"UPDATE leads SET {fields} WHERE handle = ?", params)


def matches(lead):
    min_followers = int(setting("min_followers") or 0)
    max_followers = int(setting("max_followers") or 999999999)
    site_mode = setting("site_mode")
    niche = setting("niche").strip().lower()
    if lead["status"] != "new":
        return False
    if lead["followers"] < min_followers or lead["followers"] > max_followers:
        return False
    if site_mode == "no_site" and lead["has_site"]:
        return False
    if site_mode == "has_site" and not lead["has_site"]:
        return False
    if niche and niche not in lead["niche"].lower():
        return False
    return True


def sent_today():
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT COUNT(*) FROM leads WHERE sent_at LIKE ?",
            (today() + "%",),
        ).fetchone()[0]


def render_template(template, lead):
    try:
        return template.format(
            handle=lead["handle"],
            niche=lead["niche"],
            followers=lead["followers"],
            site_status="есть сайт" if lead["has_site"] else "нет сайта",
        )
    except Exception:
        return template


def instagram_url(handle):
    return "https://www.instagram.com/" + normalize_handle(handle).lstrip("@") + "/"


def lead_key(handle):
    return normalize_handle(handle).lstrip("@")


def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "Следующий", "callback_data": "cmd|next"},
                {"text": "Follow-up", "callback_data": "cmd|followups"},
            ],
            [
                {"text": "Добавить лиды", "callback_data": "cmd|add"},
                {"text": "Статистика", "callback_data": "cmd|stats"},
            ],
            [
                {"text": "Фильтры", "callback_data": "cmd|filters"},
                {"text": "Помощь", "callback_data": "cmd|help"},
            ],
        ]
    }


def lead_card(lead, followup=False):
    template = setting("followup_template" if followup else "message_template")
    message = render_template(template, lead)
    site = "есть" if lead["has_site"] else "нет"
    title = "Follow-up" if followup else "Следующий лид"
    text = (
        f"<b>{html.escape(title)}</b>\n\n"
        f"<b>{html.escape(lead['handle'])}</b>\n"
        f"Подписчики: {lead['followers'] or '-'}\n"
        f"Ниша: {html.escape(lead['niche'] or '-')}\n"
        f"Сайт: {site}\n"
        f"Заметка: {html.escape(lead['note'] or '-')}\n\n"
        f"<b>Текст:</b>\n<pre>{html.escape(message)}</pre>"
    )
    key = lead_key(lead["handle"])
    markup = {
        "inline_keyboard": [
            [{"text": "Открыть Instagram", "url": instagram_url(lead["handle"])}],
            [{"text": "Текст отдельно", "callback_data": f"copy|{key}|{'f' if followup else 'm'}"}],
            [
                {"text": "Написал", "callback_data": f"sent|{key}"},
                {"text": "Пропустить", "callback_data": f"skip|{key}"},
            ],
            [
                {"text": "Ответил", "callback_data": f"replied|{key}"},
                {"text": "Интересно", "callback_data": f"interested|{key}"},
            ],
            [{"text": "Не подходит", "callback_data": f"bad|{key}"}],
        ]
    }
    return text, markup


def send_start(chat_id):
    send(
        chat_id,
        "Бот готов.\n\n"
        "Сценарий:\n"
        "1. /add и вставить лиды\n"
        "2. /filter min=500 max=5000 site=no_site niche=сумки\n"
        "3. /next\n"
        "4. открыть профиль, отправить текст вручную, нажать «Написал»",
        main_menu(),
    )


def send_help(chat_id):
    send(
        chat_id,
        "Команды:\n\n"
        "/add\n"
        "/next\n"
        "/filter\n"
        "/filter min=500 max=5000 site=no_site niche=сумки\n"
        "/template\n"
        "/followup_template\n"
        "/followups\n"
        "/limit 20\n"
        "/stats\n"
        "/reset_skipped\n\n"
        "Формат лидов:\n"
        "@brand.ua | 1200 | сумки | no-site | только Instagram",
        main_menu(),
    )


def send_filters(chat_id):
    send(
        chat_id,
        "Фильтры:\n\n"
        f"Подписчики: {setting('min_followers')} - {setting('max_followers')}\n"
        f"Сайт: {setting('site_mode')}\n"
        f"Ниша: {setting('niche') or 'любая'}\n"
        f"Дневной лимит: {setting('daily_limit')}\n\n"
        "Изменить:\n"
        "/filter min=500 max=5000 site=no_site niche=сумки",
        main_menu(),
    )


def apply_filter(chat_id, text):
    if text.strip() == "/filter":
        send_filters(chat_id)
        return
    body = text.split(maxsplit=1)[1]
    for key, value in re.findall(r"(min|max|site|niche)=([^=]+?)(?=\s+(?:min|max|site|niche)=|$)", body):
        value = value.strip()
        if key == "min":
            set_setting("min_followers", parse_followers(value))
        elif key == "max":
            set_setting("max_followers", parse_followers(value))
        elif key == "site":
            if value in ["no", "none", "без", "нет"]:
                value = "no_site"
            if value in ["yes", "есть", "сайт"]:
                value = "has_site"
            set_setting("site_mode", value if value in ["no_site", "has_site", "any"] else "no_site")
        elif key == "niche":
            set_setting("niche", value)
    send_filters(chat_id)


def send_next(chat_id):
    limit = int(setting("daily_limit") or 20)
    if sent_today() >= limit:
        send(chat_id, f"Дневной лимит достигнут: {sent_today()}/{limit}.", main_menu())
        return
    for lead in leads():
        if matches(lead):
            text, markup = lead_card(lead)
            send(chat_id, text, markup, parse_mode="HTML")
            return
    send(chat_id, "Нет лидов под текущие фильтры.", main_menu())


def send_followups(chat_id):
    hours = int(setting("followup_hours") or 48)
    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    for lead in leads():
        if lead["status"] != "contacted" or not lead["sent_at"] or lead["followup_at"]:
            continue
        try:
            sent_at = dt.datetime.fromisoformat(lead["sent_at"])
        except ValueError:
            continue
        if sent_at <= cutoff:
            text, markup = lead_card(lead, followup=True)
            send(chat_id, text, markup, parse_mode="HTML")
            return
    send(chat_id, "Пока нет лидов для follow-up.", main_menu())


def send_stats(chat_id):
    rows = leads()
    statuses = {}
    for lead in rows:
        statuses[lead["status"]] = statuses.get(lead["status"], 0) + 1
    ready = sum(1 for lead in rows if matches(lead))
    status_lines = "\n".join(f"{key}: {value}" for key, value in sorted(statuses.items()))
    send(
        chat_id,
        "Статистика:\n\n"
        f"Всего лидов: {len(rows)}\n"
        f"Под фильтры: {ready}\n"
        f"Отправлено сегодня: {sent_today()}/{setting('daily_limit')}\n\n"
        f"{status_lines or 'Пока пусто'}",
        main_menu(),
    )


def handle_text(chat_id, text):
    state = get_state(chat_id)
    if state == "waiting_leads":
        added, skipped = add_leads(text)
        clear_state(chat_id)
        send(chat_id, f"Добавлено: {added}. Пропущено/дубли: {skipped}.", main_menu())
        return
    if state == "waiting_template":
        set_setting("message_template", text.strip())
        clear_state(chat_id)
        send(chat_id, "Основной шаблон обновлён.", main_menu())
        return
    if state == "waiting_followup_template":
        set_setting("followup_template", text.strip())
        clear_state(chat_id)
        send(chat_id, "Follow-up шаблон обновлён.", main_menu())
        return

    if text.startswith("/start"):
        send_start(chat_id)
    elif text.startswith("/help"):
        send_help(chat_id)
    elif text.startswith("/add"):
        rest = text.split(maxsplit=1)
        if len(rest) > 1:
            added, skipped = add_leads(rest[1])
            send(chat_id, f"Добавлено: {added}. Пропущено/дубли: {skipped}.", main_menu())
        else:
            set_state(chat_id, "waiting_leads")
            send(chat_id, "Отправь лиды списком:\n@brand.ua | 1200 | сумки | no-site | заметка")
    elif text.startswith("/filter"):
        apply_filter(chat_id, text)
    elif text.startswith("/next"):
        send_next(chat_id)
    elif text.startswith("/template"):
        set_state(chat_id, "waiting_template")
        send(chat_id, "Отправь новый основной шаблон одним сообщением.")
    elif text.startswith("/followup_template"):
        set_state(chat_id, "waiting_followup_template")
        send(chat_id, "Отправь новый follow-up шаблон одним сообщением.")
    elif text.startswith("/followups"):
        send_followups(chat_id)
    elif text.startswith("/limit"):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            set_setting("daily_limit", parts[1])
            send(chat_id, f"Лимит обновлён: {parts[1]}.", main_menu())
        else:
            send(chat_id, "Пример: /limit 20")
    elif text.startswith("/stats"):
        send_stats(chat_id)
    elif text.startswith("/reset_skipped"):
        with sqlite3.connect(DB_PATH) as con:
            count = con.execute("SELECT COUNT(*) FROM leads WHERE status = 'skipped'").fetchone()[0]
            con.execute("UPDATE leads SET status = 'new' WHERE status = 'skipped'")
        send(chat_id, f"Вернул пропущенные: {count}.", main_menu())
    else:
        send(chat_id, "Не понял. Нажми /help.", main_menu())


def handle_callback(callback):
    callback_id = callback["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    answer_callback(callback_id)

    if data.startswith("cmd|"):
        cmd = data.split("|", 1)[1]
        if cmd == "next":
            send_next(chat_id)
        elif cmd == "followups":
            send_followups(chat_id)
        elif cmd == "add":
            set_state(chat_id, "waiting_leads")
            send(chat_id, "Отправь лиды списком:\n@brand.ua | 1200 | сумки | no-site | заметка")
        elif cmd == "stats":
            send_stats(chat_id)
        elif cmd == "filters":
            send_filters(chat_id)
        elif cmd == "help":
            send_help(chat_id)
        return

    parts = data.split("|")
    action = parts[0]
    handle = normalize_handle(parts[1] if len(parts) > 1 else "")
    lead = get_lead(handle)
    if not lead:
        send(chat_id, "Лид не найден.")
        return

    if action == "copy":
        template = setting("followup_template" if len(parts) > 2 and parts[2] == "f" else "message_template")
        send(chat_id, render_template(template, lead))
    elif action == "sent":
        message = render_template(setting("message_template"), lead)
        update_lead(handle, status="contacted", sent_at=now(), last_message=message)
        send(chat_id, f"Отмечено как отправленное: {handle}")
        send_next(chat_id)
    elif action == "skip":
        update_lead(handle, status="skipped")
        send(chat_id, f"Пропущено: {handle}")
        send_next(chat_id)
    elif action == "bad":
        update_lead(handle, status="not_fit")
        send(chat_id, f"Не подходит: {handle}")
        send_next(chat_id)
    elif action == "replied":
        update_lead(handle, status="replied", reply_at=now())
        send(chat_id, f"Ответил: {handle}", main_menu())
    elif action == "interested":
        update_lead(handle, status="interested", reply_at=now())
        send(chat_id, f"Интересно: {handle}", main_menu())


def run():
    if not TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in Railway Variables.")
    init_db()
    offset = 0
    print("Telegram outreach bot is running.")
    while True:
        try:
            updates = api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 50,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update and "text" in update["message"]:
                    handle_text(update["message"]["chat"]["id"], update["message"]["text"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Network error: {exc}. Retrying...")
            time.sleep(3)
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    run()
