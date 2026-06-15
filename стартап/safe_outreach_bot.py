#!/usr/bin/env python3
"""
Safe Outreach UI Bot
====================

Полноценный, но безопасный инструмент для нагрузочного тестирования сценария
"профиль -> написать сообщение -> отправить" на вашем собственном mock/staging UI.

Скрипт специально блокирует instagram.com и не содержит парсинга реальных людей.
Он подходит для передачи другому человеку: есть конфиг, меню, dry-run, CSV,
SQLite-логирование, дедупликация, статистика и команды управления во время теста.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import random
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any
from urllib.parse import urlparse


APP_NAME = "Safe Outreach UI Bot"
DEFAULT_CONFIG_PATH = "safe_outreach_config.json"
DEFAULT_DB_PATH = "safe_outreach.sqlite3"
FORBIDDEN_HOST_SUFFIXES = ("instagram.com",)


@dataclass
class BotConfig:
    target_url: str = "http://localhost:3000"
    db_path: str = DEFAULT_DB_PATH
    dry_run: bool = True
    headless: bool = False
    daily_limit: int = 20
    pause_min_seconds: int = 2
    pause_max_seconds: int = 5
    niche_keywords: list[str] = field(default_factory=lambda: ["bags", "clothes", "jewelry"])
    followers_min: int = 100
    followers_max: int = 50_000
    site_filter: str = "any"  # yes / no / any
    message_template: str = "Здравствуйте, {{username}}! Это тестовое сообщение стенда."
    selectors: dict[str, str] = field(
        default_factory=lambda: {
            "login_username": "[data-testid='login-username']",
            "login_password": "[data-testid='login-password']",
            "login_submit": "[data-testid='login-submit']",
            "message_button": "[data-testid='message-button']",
            "message_input": "[data-testid='message-input']",
            "send_message": "[data-testid='send-message']",
        }
    )


@dataclass(frozen=True)
class LeadCandidate:
    username: str
    followers: int
    has_site: bool
    niche: str


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.init_schema()

    def init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    username TEXT PRIMARY KEY,
                    followers INTEGER NOT NULL,
                    has_site INTEGER NOT NULL,
                    niche TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_contacted TEXT,
                    message_sent TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    event_type TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def add_event(self, username: str | None, event_type: str, details: str = "") -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO events(username, event_type, details, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (username, event_type, details, now()),
            )

    def upsert_lead(self, lead: LeadCandidate, status: str = "new") -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO leads(
                    username, followers, has_site, niche, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (lead.username, lead.followers, int(lead.has_site), lead.niche, status, now()),
            )
            return cursor.rowcount == 1

    def mark_sent(self, username: str, message: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                UPDATE leads
                SET status = 'sent',
                    attempts = attempts + 1,
                    last_error = NULL,
                    last_contacted = ?,
                    message_sent = ?
                WHERE username = ?
                """,
                (now(), message, username),
            )

    def mark_rejected(self, username: str, error: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                UPDATE leads
                SET status = 'rejected',
                    attempts = attempts + 1,
                    last_error = ?
                WHERE username = ?
                """,
                (error[:500], username),
            )

    def already_finished(self, username: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT status FROM leads WHERE username = ?", (username,)
            ).fetchone()
        return row is not None and row["status"] in {"sent", "replied", "rejected"}

    def daily_sent_count(self) -> int:
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM leads
                WHERE status = 'sent' AND substr(last_contacted, 1, 10) = ?
                """,
                (date.today().isoformat(),),
            ).fetchone()
        return int(row["cnt"])

    def stats(self) -> dict[str, int]:
        stats = {"new": 0, "sent": 0, "replied": 0, "rejected": 0, "events": 0}
        with self.lock:
            for row in self.conn.execute("SELECT status, COUNT(*) AS cnt FROM leads GROUP BY status"):
                stats[row["status"]] = int(row["cnt"])
            event_row = self.conn.execute("SELECT COUNT(*) AS cnt FROM events").fetchone()
            stats["events"] = int(event_row["cnt"])
        return stats

    def export_csv(self, path: str) -> None:
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT username, followers, has_site, niche, status, attempts,
                       last_error, last_contacted, message_sent, created_at
                FROM leads
                ORDER BY created_at DESC
                """
            ).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(rows[0].keys() if rows else [
                "username", "followers", "has_site", "niche", "status", "attempts",
                "last_error", "last_contacted", "message_sent", "created_at",
            ])
            for row in rows:
                writer.writerow([row[key] for key in row.keys()])

    def close(self) -> None:
        self.conn.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_default_config(path: str) -> None:
    config_path = Path(path)
    if config_path.exists():
        print(f"Конфиг уже существует: {config_path.resolve()}")
        return
    config_path.write_text(
        json.dumps(asdict(BotConfig()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Создан конфиг: {config_path.resolve()}")


def load_config(path: str) -> BotConfig:
    config_path = Path(path)
    if not config_path.exists():
        save_default_config(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = BotConfig(**{**asdict(BotConfig()), **raw})
    validate_config(config)
    return config


def validate_config(config: BotConfig) -> None:
    host = (urlparse(config.target_url).hostname or "").lower()
    if any(host == suffix or host.endswith("." + suffix) for suffix in FORBIDDEN_HOST_SUFFIXES):
        raise ValueError("Этот инструмент не запускается против Instagram.")
    if config.pause_min_seconds > config.pause_max_seconds:
        raise ValueError("pause_min_seconds не может быть больше pause_max_seconds.")
    if config.daily_limit < 1:
        raise ValueError("daily_limit должен быть больше 0.")
    if config.site_filter not in {"yes", "no", "any"}:
        raise ValueError("site_filter должен быть yes, no или any.")


def interactive_setup(config: BotConfig) -> tuple[str, str, BotConfig]:
    print(f"\n=== {APP_NAME} ===")
    print("Нажмите Enter, чтобы оставить значение из конфига.")
    target_url = ask_str("URL тестового стенда", config.target_url)
    login = ask_str("Логин тестового аккаунта", "")
    password = getpass.getpass("Пароль тестового аккаунта: ")
    keywords = ask_str("Ниши через запятую", ", ".join(config.niche_keywords))
    followers_min = ask_int("Подписчиков от", config.followers_min, 0)
    followers_max = ask_int("Подписчиков до", config.followers_max, followers_min)
    site_filter = ask_choice("Сайт в профиле", ["yes", "no", "any"], config.site_filter)
    daily_limit = ask_int("Дневной лимит", config.daily_limit, 1)
    message_template = ask_str("Шаблон сообщения", config.message_template)

    updated = BotConfig(**asdict(config))
    updated.target_url = target_url
    updated.niche_keywords = [item.strip() for item in keywords.split(",") if item.strip()]
    updated.followers_min = followers_min
    updated.followers_max = followers_max
    updated.site_filter = site_filter
    updated.daily_limit = daily_limit
    updated.message_template = message_template
    validate_config(updated)
    return login, password, updated


def ask_str(prompt: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_int(prompt: str, default: int, min_value: int) -> int:
    while True:
        value = ask_str(prompt, str(default))
        try:
            parsed = int(value)
        except ValueError:
            print("Введите целое число.")
            continue
        if parsed >= min_value:
            return parsed
        print(f"Значение должно быть не меньше {min_value}.")


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    while True:
        value = ask_str(f"{prompt} ({'/'.join(choices)})", default).lower()
        if value in choices:
            return value
        print("Выберите один из вариантов.")


def import_csv(path: str, storage: Storage) -> int:
    imported = 0
    with open(path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            username = (row.get("username") or "").strip().lstrip("@")
            if not username:
                continue
            lead = LeadCandidate(
                username=username,
                followers=int(row.get("followers") or 0),
                has_site=str(row.get("has_site") or "").lower() in {"1", "true", "yes", "да"},
                niche=(row.get("niche") or "unknown").strip(),
            )
            imported += int(storage.upsert_lead(lead))
    storage.add_event(None, "import_csv", f"path={path}, imported={imported}")
    return imported


def generate_demo_leads(config: BotConfig) -> list[LeadCandidate]:
    leads: list[LeadCandidate] = []
    for index in range(config.daily_limit * 6):
        niche = random.choice(config.niche_keywords)
        lead = LeadCandidate(
            username=f"test_{niche}_{index:04d}".replace(" ", "_"),
            followers=random.randint(max(0, config.followers_min - 500), config.followers_max + 5_000),
            has_site=random.choice([True, False]),
            niche=niche,
        )
        if lead_matches(lead, config):
            leads.append(lead)
        if len(leads) >= config.daily_limit:
            break
    return leads


def lead_matches(lead: LeadCandidate, config: BotConfig) -> bool:
    if not config.followers_min <= lead.followers <= config.followers_max:
        return False
    if config.site_filter == "yes" and not lead.has_site:
        return False
    if config.site_filter == "no" and lead.has_site:
        return False
    return True


class CommandCenter:
    def __init__(self) -> None:
        self.queue: Queue[str] = Queue()
        self.stop_requested = False
        self.paused = False
        self.thread = threading.Thread(target=self._reader, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _reader(self) -> None:
        while True:
            try:
                command = input().strip().lower()
            except EOFError:
                self.queue.put("stop")
                return
            if command:
                self.queue.put(command)

    def process(self, storage: Storage) -> None:
        while True:
            try:
                command = self.queue.get_nowait()
            except Empty:
                return
            if command == "status":
                print_stats(storage)
            elif command == "pause":
                self.paused = True
                print("Пауза включена. Введите resume, чтобы продолжить.")
            elif command == "resume":
                self.paused = False
                print("Продолжаем.")
            elif command == "stop":
                self.stop_requested = True
                print("Остановка запрошена.")
            elif command.startswith("export "):
                path = command.removeprefix("export ").strip() or "leads_export.csv"
                storage.export_csv(path)
                print(f"Экспортировано: {Path(path).resolve()}")
            else:
                print("Команды: status, pause, resume, stop, export leads.csv")


def print_stats(storage: Storage) -> None:
    stats = storage.stats()
    print(
        "Статистика: "
        f"new={stats['new']}, sent={stats['sent']}, replied={stats['replied']}, "
        f"rejected={stats['rejected']}, events={stats['events']}"
    )


def wait_with_controls(seconds: int, commands: CommandCenter, storage: Storage) -> bool:
    finish_at = time.time() + seconds
    while time.time() < finish_at:
        commands.process(storage)
        if commands.stop_requested:
            return False
        while commands.paused and not commands.stop_requested:
            commands.process(storage)
            time.sleep(0.5)
        time.sleep(0.5)
    return True


def send_message_dry_run(storage: Storage, lead: LeadCandidate, message: str) -> bool:
    storage.add_event(lead.username, "open_profile", "dry-run")
    storage.add_event(lead.username, "open_message_dialog", "dry-run")
    storage.add_event(lead.username, "type_message", f"{len(message)} chars")
    storage.add_event(lead.username, "submit_message", "dry-run")
    return True


def send_message_playwright(config: BotConfig, login: str, password: str, leads: list[LeadCandidate], storage: Storage) -> None:
    """
    Playwright подключается лениво, чтобы dry-run работал без установки браузера.
    Селекторы берутся из config.selectors и должны существовать на вашем стенде.
    """
    import asyncio
    from playwright.async_api import async_playwright

    async def runner() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=config.headless)
            page = await browser.new_page()
            await page.goto(config.target_url, wait_until="domcontentloaded")

            username_input = page.locator(config.selectors["login_username"])
            if await username_input.count() > 0:
                await username_input.fill(login)
                await page.locator(config.selectors["login_password"]).fill(password)
                await page.locator(config.selectors["login_submit"]).click()
                await page.wait_for_load_state("networkidle")

            commands = CommandCenter()
            commands.start()
            print("Команды во время выполнения: status, pause, resume, stop, export leads.csv")

            sent = 0
            for lead in leads:
                commands.process(storage)
                if commands.stop_requested or storage.daily_sent_count() >= config.daily_limit:
                    break
                if storage.already_finished(lead.username):
                    continue

                storage.upsert_lead(lead)
                message = config.message_template.replace("{{username}}", lead.username)
                ok = await send_one_with_page(page, config, lead, message, storage)
                if ok:
                    sent += 1
                    storage.mark_sent(lead.username, message)
                    print(f"Отправлено {sent}/{config.daily_limit}: @{lead.username}")
                else:
                    storage.mark_rejected(lead.username, "UI selectors not found or send failed")
                    print(f"Не отправлено: @{lead.username}")

                delay = random.randint(config.pause_min_seconds, config.pause_max_seconds)
                print(f"Пауза {delay} сек.")
                if not wait_with_controls(delay, commands, storage):
                    break

            await browser.close()

    asyncio.run(runner())


async def send_one_with_page(page: Any, config: BotConfig, lead: LeadCandidate, message: str, storage: Storage) -> bool:
    profile_url = f"{config.target_url.rstrip('/')}/profiles/{lead.username}"
    await page.goto(profile_url, wait_until="domcontentloaded")
    storage.add_event(lead.username, "open_profile", profile_url)

    message_button = page.locator(config.selectors["message_button"])
    if await message_button.count() == 0:
        storage.add_event(lead.username, "send_failed", "message button not found")
        return False

    await message_button.click()
    await page.locator(config.selectors["message_input"]).fill(message)
    await page.locator(config.selectors["send_message"]).click()
    await page.wait_for_timeout(500)
    storage.add_event(lead.username, "submit_message", "ok")
    return True


def run_dry(config: BotConfig, leads: list[LeadCandidate], storage: Storage) -> None:
    commands = CommandCenter()
    commands.start()
    print("Команды во время выполнения: status, pause, resume, stop, export leads.csv")
    sent = 0

    for lead in leads:
        commands.process(storage)
        if commands.stop_requested or storage.daily_sent_count() >= config.daily_limit:
            break
        if storage.already_finished(lead.username):
            continue

        storage.upsert_lead(lead)
        message = config.message_template.replace("{{username}}", lead.username)
        ok = send_message_dry_run(storage, lead, message)
        if ok:
            sent += 1
            storage.mark_sent(lead.username, message)
            print(f"Dry-run отправка {sent}/{config.daily_limit}: @{lead.username}")
        else:
            storage.mark_rejected(lead.username, "dry-run failed")

        delay = random.randint(config.pause_min_seconds, config.pause_max_seconds)
        print(f"Пауза {delay} сек.")
        if not wait_with_controls(delay, commands, storage):
            break

    print_stats(storage)


def run_bot(config: BotConfig, login: str, password: str, storage: Storage) -> None:
    leads = generate_demo_leads(config)
    storage.add_event(None, "run_started", f"dry_run={config.dry_run}, leads={len(leads)}")
    print(f"Подготовлено тестовых лидов: {len(leads)}")

    if config.dry_run:
        run_dry(config, leads, storage)
    else:
        send_message_playwright(config, login, password, leads, storage)

    storage.add_event(None, "run_finished")
    print("Готово.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Путь к JSON-конфигу.")
    parser.add_argument("--init-config", action="store_true", help="Создать пример конфига и выйти.")
    parser.add_argument("--run", action="store_true", help="Запустить тест без интерактивного меню.")
    parser.add_argument("--dry-run", action="store_true", help="Принудительно включить dry-run.")
    parser.add_argument("--live-staging", action="store_true", help="Запустить против собственного staging UI.")
    parser.add_argument("--stats", action="store_true", help="Показать статистику.")
    parser.add_argument("--import-csv", help="Импортировать лиды из CSV.")
    parser.add_argument("--export-csv", help="Экспортировать лиды в CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.init_config:
        save_default_config(args.config)
        return 0

    try:
        config = load_config(args.config)
        if args.dry_run:
            config.dry_run = True
        if args.live_staging:
            config.dry_run = False
        validate_config(config)
        storage = Storage(config.db_path)

        if args.import_csv:
            count = import_csv(args.import_csv, storage)
            print(f"Импортировано новых лидов: {count}")
        if args.export_csv:
            storage.export_csv(args.export_csv)
            print(f"Экспортировано: {Path(args.export_csv).resolve()}")
        if args.stats:
            print_stats(storage)
        if args.import_csv or args.export_csv or args.stats:
            storage.close()
            return 0

        if args.run:
            login = input("Логин тестового аккаунта: ").strip()
            password = getpass.getpass("Пароль тестового аккаунта: ")
            run_bot(config, login, password, storage)
        else:
            login, password, interactive_config = interactive_setup(config)
            print("\nПроверьте настройки:")
            print(f"Стенд: {interactive_config.target_url}")
            print(f"Режим: {'dry-run' if interactive_config.dry_run else 'staging UI'}")
            print(f"Лимит: {interactive_config.daily_limit}")
            print(f"Ниши: {', '.join(interactive_config.niche_keywords)}")
            if input("Запустить? [y/N]: ").strip().lower() == "y":
                run_bot(interactive_config, login, password, storage)
            else:
                print("Отменено.")

        storage.close()
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 130
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
