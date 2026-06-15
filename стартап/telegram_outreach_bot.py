import datetime as dt
import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request


DB_PATH = os.environ.get("OUTREACH_BOT_DB", "outreach_bot.sqlite3")


def load_token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    token_file = os.environ.get("TELEGRAM_BOT_TOKEN_FILE", "telegram_bot_token.txt")
    if os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as file:
            return file.read().strip()
    return ""


TOKEN = load_token()
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


def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today_iso():
    return dt.date.today().isoformat()


def api(method, payload=None):
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN first.")

    data = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=70) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result)
    return result["result"]


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
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
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
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
            """
            CREATE TABLE IF NOT EXISTS user_state (
                chat_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            con.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )


def get_setting(key):
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


def set_state(chat_id, state, payload=""):
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO user_state(chat_id, state, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET state = excluded.state, payload = excluded.payload",
            (str(chat_id), state, payload),
        )


def get_state(chat_id):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT state, payload FROM user_state WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()
    return row if row else ("", "")


def clear_state(chat_id):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM user_state WHERE chat_id = ?", (str(chat_id),))


def normalize_handle(value):
    value = value.strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I)
    value = value.split("?")[0].split("/")[0].strip()
    value = value.lstrip("@").lower()
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
    if not match:
        return 0
    return int(float(match.group(0)) * multiplier)


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
    if not handle or handle == "@":
        return None
    return {
        "handle": handle,
        "followers": parse_followers(parts[1] if len(parts) > 1 else ""),
        "niche": parts[2] if len(parts) > 2 else "",
        "has_site": parse_site(parts[3] if len(parts) > 3 else ""),
        "note": " | ".join(parts[4:]) if len(parts) > 4 else "",
    }


def add_leads_from_text(text):
    added = 0
    skipped = 0
    now = now_iso()
    with sqlite3.connect(DB_PATH) as con:
        existing = {
            row[0]
            for row in con.execute("SELECT handle FROM leads").fetchall()
        }
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
                    now,
                ),
            )
            existing.add(lead["handle"])
            added += 1
    return added, skipped


def all_leads():
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute("SELECT * FROM leads ORDER BY created_at").fetchall()]


def get_lead(handle):
    handle = normalize_handle(handle)
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM leads WHERE handle = ?", (handle,)).fetchone()
    return dict(row) if row else None


def update_lead(handle, **values):
    handle = normalize_handle(handle)
    if not values:
        return
    fields = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [handle]
    with sqlite3.connect(DB_PATH) as con:
        con.execute(f"UPDATE leads SET {fields} WHERE handle = ?", params)


def filter_lead(lead):
    min_followers = int(get_setting("min_followers") or 0)
    max_followers = int(get_setting("max_followers") or 999999999)
    site_mode = get_setting("site_mode")
    niche = get_setting("niche").strip().lower()

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


def sent_today_count():
    with sqlite3.connect(DB_PATH) as con:
        return con.execute(
            "SELECT COUNT(*) FROM leads WHERE sent_at LIKE ?",
            (today_iso() + "%",),
        ).fetchone()[0]


def next_lead():
    for lead in all_leads():
        if filter_lead(lead):
            return lead
    return None


def render_template(template, lead):
    site_status = "есть сайт" if lead["has_site"] else "нет сайта"
    try:
        return template.format(
            handle=lead["handle"],
            niche=lead["niche"],
            followers=lead["followers"],
            site_status=site_status,
        )
    except Exception:
        return template


def instagram_url(handle):
    return "https://www.instagram.com/" + normalize_handle(handle).lstrip("@") + "/"


def lead_key(handle):
    return normalize_handle(handle).lstrip("@")


def lead_card(lead, followup=False):
    template = get_setting("followup_template" if followup else "message_template")
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


def menu_markup():
    return {
        "inline_keyboard": [
            [
                {"text": "Следующий лид", "callback_data": "cmd|next"},
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


def send_start(chat_id):
    text = (
        "Готово. Это бот для полуавтоматического outreach.\n\n"
        "Он не отправляет Direct сам. Он хранит лиды, фильтрует их, выдаёт следующий профиль, "
        "даёт текст и кнопки статусов.\n\n"
        "Самый короткий сценарий:\n"
        "1. /add и вставить список лидов\n"
        "2. /filter min=500 max=5000 site=no_site niche=сумки\n"
        "3. /next\n"
        "4. открыть профиль, отправить текст вручную, нажать «Написал»"
    )
    send_message(chat_id, text, menu_markup())


def send_help(chat_id):
    text = (
        "Команды:\n\n"
        "/add - добавить лиды списком\n"
        "/next - получить следующий аккаунт\n"
        "/filter - показать фильтры\n"
        "/filter min=500 max=5000 site=no_site niche=сумки - задать фильтры\n"
        "/template - заменить основной текст\n"
        "/followup_template - заменить текст follow-up\n"
        "/followups - аккаунты, кому пора написать повторно\n"
        "/limit 20 - дневной лимит\n"
        "/stats - статистика\n"
        "/reset_skipped - вернуть пропущенные в очередь\n\n"
        "Формат лидов:\n"
        "@brand.ua | 1200 | сумки | no-site | только Instagram\n"
        "@jewelry.ua | 3400 | украшения | has-site | сайт слабый\n\n"
        "Подписчиков можно писать как 1200, 1.2k, 3к, 4 тыс."
    )
    send_message(chat_id, text, menu_markup())


def send_filters(chat_id):
    text = (
        "Текущие фильтры:\n\n"
        f"Подписчики: {get_setting('min_followers')} - {get_setting('max_followers')}\n"
        f"Сайт: {get_setting('site_mode')}\n"
        f"Ниша: {get_setting('niche') or 'любая'}\n"
        f"Дневной лимит: {get_setting('daily_limit')}\n\n"
        "Изменить:\n"
        "/filter min=500 max=5000 site=no_site niche=сумки\n\n"
        "site варианты: no_site, has_site, any"
    )
    send_message(chat_id, text, menu_markup())


def apply_filter_command(chat_id, text):
    args = text.split(maxsplit=1)
    if len(args) == 1:
        send_filters(chat_id)
        return

    body = args[1]
    updates = {}
    for key, value in re.findall(r"(min|max|site|niche)=([^=]+?)(?=\s+(?:min|max|site|niche)=|$)", body):
        value = value.strip()
        if key == "min":
            updates["min_followers"] = str(parse_followers(value))
        elif key == "max":
            updates["max_followers"] = str(parse_followers(value))
        elif key == "site":
            if value in ["no", "none", "без", "нет"]:
                value = "no_site"
            if value in ["yes", "есть", "сайт"]:
                value = "has_site"
            updates["site_mode"] = value if value in ["no_site", "has_site", "any"] else "no_site"
        elif key == "niche":
            updates["niche"] = value

    for key, value in updates.items():
        set_setting(key, value)

    send_filters(chat_id)


def send_add_help(chat_id):
    set_state(chat_id, "waiting_leads")
    text = (
        "Отправь лиды одним сообщением.\n\n"
        "Формат:\n"
        "@brand.ua | 1200 | сумки | no-site | только Instagram\n"
        "@jewelry.ua | 3400 | украшения | has-site | сайт слабый\n\n"
        "Я уберу дубли и добавлю только новые аккаунты."
    )
    send_message(chat_id, text)


def send_template_help(chat_id, followup=False):
    set_state(chat_id, "waiting_followup_template" if followup else "waiting_template")
    current = get_setting("followup_template" if followup else "message_template")
    title = "follow-up" if followup else "основной"
    text = (
        f"Отправь новый {title} шаблон одним сообщением.\n\n"
        "Можно использовать переменные:\n"
        "{handle}, {niche}, {followers}, {site_status}\n\n"
        "Текущий шаблон:\n"
        f"{current}"
    )
    send_message(chat_id, text)


def send_next(chat_id):
    limit = int(get_setting("daily_limit") or 20)
    if sent_today_count() >= limit:
        send_message(
            chat_id,
            f"Дневной лимит достигнут: {sent_today_count()}/{limit}. Лучше продолжить завтра.",
            menu_markup(),
        )
        return

    lead = next_lead()
    if not lead:
        send_message(chat_id, "Нет лидов под текущие фильтры. Добавь лиды или измени /filter.", menu_markup())
        return

    text, markup = lead_card(lead)
    send_message(chat_id, text, markup, parse_mode="HTML")


def send_followups(chat_id):
    hours = int(get_setting("followup_hours") or 48)
    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    leads = []
    for lead in all_leads():
        if lead["status"] != "contacted" or not lead["sent_at"] or lead["followup_at"]:
            continue
        try:
            sent_at = dt.datetime.fromisoformat(lead["sent_at"])
        except ValueError:
            continue
        if sent_at <= cutoff:
            leads.append(lead)

    if not leads:
        send_message(chat_id, "Пока нет лидов для follow-up.", menu_markup())
        return

    text, markup = lead_card(leads[0], followup=True)
    send_message(chat_id, text, markup, parse_mode="HTML")


def send_stats(chat_id):
    leads = all_leads()
    by_status = {}
    for lead in leads:
        by_status[lead["status"]] = by_status.get(lead["status"], 0) + 1
    ready = sum(1 for lead in leads if filter_lead(lead))
    text = (
        "Статистика:\n\n"
        f"Всего лидов: {len(leads)}\n"
        f"Под текущие фильтры: {ready}\n"
        f"Отправлено сегодня: {sent_today_count()}/{get_setting('daily_limit')}\n\n"
        "По статусам:\n"
        + "\n".join(f"{status}: {count}" for status, count in sorted(by_status.items()))
    )
    send_message(chat_id, text, menu_markup())


def reset_skipped(chat_id):
    with sqlite3.connect(DB_PATH) as con:
        count = con.execute("SELECT COUNT(*) FROM leads WHERE status = 'skipped'").fetchone()[0]
        con.execute("UPDATE leads SET status = 'new' WHERE status = 'skipped'")
    send_message(chat_id, f"Вернул в очередь пропущенные лиды: {count}.", menu_markup())


def handle_text(chat_id, text):
    state, _ = get_state(chat_id)

    if state == "waiting_leads":
        added, skipped = add_leads_from_text(text)
        clear_state(chat_id)
        send_message(chat_id, f"Добавлено: {added}. Пропущено/дубли: {skipped}.", menu_markup())
        return

    if state == "waiting_template":
        set_setting("message_template", text.strip())
        clear_state(chat_id)
        send_message(chat_id, "Основной шаблон обновлён.", menu_markup())
        return

    if state == "waiting_followup_template":
        set_setting("followup_template", text.strip())
        clear_state(chat_id)
        send_message(chat_id, "Follow-up шаблон обновлён.", menu_markup())
        return

    if text.startswith("/start"):
        send_start(chat_id)
    elif text.startswith("/help"):
        send_help(chat_id)
    elif text.startswith("/add"):
        rest = text.split(maxsplit=1)
        if len(rest) > 1:
            added, skipped = add_leads_from_text(rest[1])
            send_message(chat_id, f"Добавлено: {added}. Пропущено/дубли: {skipped}.", menu_markup())
        else:
            send_add_help(chat_id)
    elif text.startswith("/filter"):
        apply_filter_command(chat_id, text)
    elif text.startswith("/next"):
        send_next(chat_id)
    elif text.startswith("/template"):
        send_template_help(chat_id)
    elif text.startswith("/followup_template"):
        send_template_help(chat_id, followup=True)
    elif text.startswith("/followups"):
        send_followups(chat_id)
    elif text.startswith("/stats"):
        send_stats(chat_id)
    elif text.startswith("/limit"):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            set_setting("daily_limit", parts[1])
            send_message(chat_id, f"Дневной лимит обновлён: {parts[1]}.", menu_markup())
        else:
            send_message(chat_id, "Пример: /limit 20")
    elif text.startswith("/reset_skipped"):
        reset_skipped(chat_id)
    else:
        send_message(chat_id, "Не понял команду. Нажми /help.", menu_markup())


def handle_callback(callback):
    callback_id = callback["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    answer_callback(callback_id)

    if data.startswith("cmd|"):
        command = data.split("|", 1)[1]
        if command == "next":
            send_next(chat_id)
        elif command == "followups":
            send_followups(chat_id)
        elif command == "add":
            send_add_help(chat_id)
        elif command == "stats":
            send_stats(chat_id)
        elif command == "filters":
            send_filters(chat_id)
        elif command == "help":
            send_help(chat_id)
        return

    parts = data.split("|")
    action = parts[0]
    handle = normalize_handle(parts[1] if len(parts) > 1 else "")
    lead = get_lead(handle)
    if not lead:
        send_message(chat_id, "Лид не найден.")
        return

    if action == "copy":
        followup = len(parts) > 2 and parts[2] == "f"
        template = get_setting("followup_template" if followup else "message_template")
        send_message(chat_id, render_template(template, lead))
        return

    if action == "sent":
        message = render_template(get_setting("message_template"), lead)
        update_lead(handle, status="contacted", sent_at=now_iso(), last_message=message)
        send_message(chat_id, f"Отметил как отправленное: {handle}")
        send_next(chat_id)
        return

    if action == "skip":
        update_lead(handle, status="skipped")
        send_message(chat_id, f"Пропустил: {handle}")
        send_next(chat_id)
        return

    if action == "bad":
        update_lead(handle, status="not_fit")
        send_message(chat_id, f"Отметил как не подходит: {handle}")
        send_next(chat_id)
        return

    if action == "replied":
        update_lead(handle, status="replied", reply_at=now_iso())
        send_message(chat_id, f"Отметил ответ: {handle}", menu_markup())
        return

    if action == "interested":
        update_lead(handle, status="interested", reply_at=now_iso())
        send_message(chat_id, f"Отметил интерес: {handle}", menu_markup())
        return


def run():
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
                    chat_id = update["message"]["chat"]["id"]
                    handle_text(chat_id, update["message"]["text"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Network error: {exc}. Retrying...")
            time.sleep(3)
        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    if not TOKEN:
        print("Telegram bot token is missing.")
        print("Create telegram_bot_token.txt in this folder and paste your token there.")
        print("Then run start_telegram_bot.bat or python telegram_outreach_bot.py.")
        raise SystemExit(1)
    run()
