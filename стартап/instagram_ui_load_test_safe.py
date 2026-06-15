#!/usr/bin/env python3
"""
Безопасный консольный инструмент для нагрузочного тестирования UI сценария
"профиль -> сообщение -> отправка" на собственном тестовом стенде.

Важно: скрипт намеренно не работает с instagram.com, не парсит реальных
пользователей и не помогает маскировать массовую рассылку. Для проверки
производительности используйте dry-run или URL вашего mock/staging UI.
"""

from __future__ import annotations

import argparse
import getpass
import random
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from queue import Queue
from typing import Iterable
from urllib.parse import urlparse


DEFAULT_DAILY_LIMIT = 20
DEFAULT_DB_PATH = "ui_load_test.sqlite3"
FORBIDDEN_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}


@dataclass(frozen=True)
class Filters:
    niche_keywords: list[str]
    followers_min: int
    followers_max: int
    website_required: str  # yes / no / any
    daily_limit: int


@dataclass(frozen=True)
class Profile:
    username: str
    niche: str
    followers: int
    has_website: bool
    is_business: bool = True
    is_public: bool = True


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    username TEXT PRIMARY KEY,
                    niche TEXT NOT NULL,
                    followers INTEGER NOT NULL,
                    has_website INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    first_seen_at TEXT NOT NULL,
                    last_action_at TEXT,
                    message TEXT
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
                (username, event_type, details, datetime.now().isoformat(timespec="seconds")),
            )

    def upsert_new_profile(self, profile: Profile) -> bool:
        """Возвращает True, если профиль добавлен впервые."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO profiles(
                    username, niche, followers, has_website, status, first_seen_at
                )
                VALUES (?, ?, ?, ?, 'new', ?)
                """,
                (
                    profile.username,
                    profile.niche,
                    profile.followers,
                    int(profile.has_website),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return cur.rowcount == 1

    def mark_sent(self, username: str, message: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                UPDATE profiles
                SET status = 'sent', last_action_at = ?, message = ?
                WHERE username = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), message, username),
            )

    def already_tested(self, username: str) -> bool:
        with self.lock:
            row = self.conn.execute(
                "SELECT status FROM profiles WHERE username = ?", (username,)
            ).fetchone()
        return row is not None and row["status"] in {"sent", "replied", "rejected"}

    def daily_sent_count(self) -> int:
        today = date.today().isoformat()
        with self.lock:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM profiles
                WHERE status = 'sent' AND substr(last_action_at, 1, 10) = ?
                """,
                (today,),
            ).fetchone()
        return int(row["cnt"])

    def stats(self) -> dict[str, int]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM profiles GROUP BY status"
            ).fetchall()
        result = {"new": 0, "sent": 0, "replied": 0, "rejected": 0}
        for row in rows:
            result[row["status"]] = int(row["cnt"])
        result["events"] = self.event_count()
        return result

    def event_count(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS cnt FROM events").fetchone()
        return int(row["cnt"])

    def close(self) -> None:
        self.conn.close()


def ask_int(prompt: str, default: int | None = None, min_value: int = 0) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
            if value >= min_value:
                return value
        except ValueError:
            pass
        print(f"Введите целое число не меньше {min_value}.")


def ask_choice(prompt: str, choices: dict[str, str], default: str) -> str:
    labels = " / ".join(f"{key}={value}" for key, value in choices.items())
    while True:
        raw = input(f"{prompt} ({labels}) [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print("Выберите один из доступных вариантов.")


def collect_settings() -> tuple[str, str, Filters, str]:
    print("\n=== Настройка теста UI ===")
    login = input("Логин тестового аккаунта: ").strip()
    password = getpass.getpass("Пароль тестового аккаунта: ")

    print("\n=== Фильтры тестовых профилей ===")
    keywords_raw = input("Ниша, ключевые слова через запятую: ").strip()
    keywords = [part.strip().lower() for part in keywords_raw.split(",") if part.strip()]
    if not keywords:
        keywords = ["bags", "clothes", "jewelry"]

    followers_min = ask_int("Подписчики от", default=100, min_value=0)
    followers_max = ask_int("Подписчики до", default=50_000, min_value=followers_min)
    website_required = ask_choice(
        "Сайт в шапке",
        {"yes": "да", "no": "нет", "any": "неважно"},
        default="any",
    )
    daily_limit = ask_int(
        "Максимум профилей за день", default=DEFAULT_DAILY_LIMIT, min_value=1
    )

    print("\n=== Шаблон сообщения ===")
    message_template = input(
        "Сообщение, можно использовать {{username}}: "
    ).strip()
    if not message_template:
        message_template = "Здравствуйте, {{username}}! Это тестовое сообщение стенда."

    filters = Filters(
        niche_keywords=keywords,
        followers_min=followers_min,
        followers_max=followers_max,
        website_required=website_required,
        daily_limit=daily_limit,
    )
    return login, password, filters, message_template


def confirm_settings(filters: Filters, message_template: str, dry_run: bool, target_url: str | None) -> bool:
    print("\n=== Подтверждение ===")
    print(f"Ниши: {', '.join(filters.niche_keywords)}")
    print(f"Подписчики: {filters.followers_min}..{filters.followers_max}")
    print(f"Сайт в шапке: {filters.website_required}")
    print(f"Дневной лимит: {filters.daily_limit}")
    print(f"Режим: {'dry-run' if dry_run else 'staging UI'}")
    if target_url:
        print(f"Тестовый URL: {target_url}")
    print(f"Шаблон: {message_template}")
    return input("Запустить тест? [y/N]: ").strip().lower() == "y"


def ensure_safe_target(target_url: str | None) -> None:
    if not target_url:
        return
    host = urlparse(target_url).hostname or ""
    if host.lower() in FORBIDDEN_HOSTS or host.lower().endswith(".instagram.com"):
        raise ValueError("Этот инструмент не запускается против Instagram.")


def generate_candidate_profiles(filters: Filters, count: int) -> Iterable[Profile]:
    """
    Генератор синтетических профилей для тестов.
    При необходимости замените этот слой чтением из CSV вашего тестового стенда.
    """
    words = filters.niche_keywords
    for idx in range(count * 4):
        niche = random.choice(words)
        followers = random.randint(
            max(0, filters.followers_min - 500), filters.followers_max + 5_000
        )
        has_website = random.choice([True, False])
        username = f"test_{niche}_{idx:04d}".replace(" ", "_")
        profile = Profile(username=username, niche=niche, followers=followers, has_website=has_website)
        if profile_matches(profile, filters):
            yield profile


def profile_matches(profile: Profile, filters: Filters) -> bool:
    if not profile.is_business or not profile.is_public:
        return False
    if profile.followers < filters.followers_min or profile.followers > filters.followers_max:
        return False
    if filters.website_required == "yes" and not profile.has_website:
        return False
    if filters.website_required == "no" and profile.has_website:
        return False
    return True


class UiClient:
    """Абстракция UI: dry-run по умолчанию, staging URL можно подключить отдельно."""

    def __init__(self, target_url: str | None, dry_run: bool, storage: Storage) -> None:
        self.target_url = target_url
        self.dry_run = dry_run
        self.storage = storage

    def login(self, username: str, password: str) -> None:
        details = f"user={username}, mode={'dry-run' if self.dry_run else self.target_url}"
        self.storage.add_event(None, "login", details)
        print("Вход в тестовый стенд выполнен.")

    def send_message(self, profile: Profile, message: str) -> None:
        self.open_profile(profile)
        self.open_message_dialog(profile)
        self.type_message(profile, message)
        self.submit_message(profile)

    def open_profile(self, profile: Profile) -> None:
        self.storage.add_event(profile.username, "open_profile", profile.niche)
        print(f"Открыт профиль @{profile.username}")

    def open_message_dialog(self, profile: Profile) -> None:
        self.storage.add_event(profile.username, "open_message_dialog")
        print(f"Открыт диалог @{profile.username}")

    def type_message(self, profile: Profile, message: str) -> None:
        self.storage.add_event(profile.username, "type_message", f"{len(message)} chars")
        print(f"Введено сообщение для @{profile.username}")

    def submit_message(self, profile: Profile) -> None:
        self.storage.add_event(profile.username, "submit_message")
        print(f"Сообщение отправлено @{profile.username}")


def wait_with_commands(seconds: int, command_queue: Queue[str], stop_event: threading.Event) -> None:
    finish_at = time.time() + seconds
    while time.time() < finish_at:
        if stop_event.is_set():
            return
        try:
            command = command_queue.get(timeout=1).strip().lower()
        except Exception:
            continue
        if command == "stop":
            stop_event.set()
            return
        if command:
            command_queue.put(command)
            return


def command_reader(command_queue: Queue[str], stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            command = input().strip().lower()
        except EOFError:
            stop_event.set()
            return
        if command:
            command_queue.put(command)
        if command == "stop":
            stop_event.set()


def print_status(storage: Storage) -> None:
    stats = storage.stats()
    print(
        "Статистика: "
        f"new={stats['new']}, sent={stats['sent']}, "
        f"replied={stats['replied']}, rejected={stats['rejected']}, events={stats['events']}"
    )


def run_test(
    login: str,
    password: str,
    filters: Filters,
    message_template: str,
    db_path: str,
    target_url: str | None,
    dry_run: bool,
    min_pause: int,
    max_pause: int,
) -> None:
    ensure_safe_target(target_url)
    storage = Storage(db_path)
    command_queue: Queue[str] = Queue()
    stop_event = threading.Event()
    reader = threading.Thread(
        target=command_reader, args=(command_queue, stop_event), daemon=True
    )
    reader.start()

    try:
        client = UiClient(target_url=target_url, dry_run=dry_run, storage=storage)
        client.login(login, password)
        storage.add_event(None, "test_started")
        print("Во время теста доступны команды: status, stop")

        for profile in generate_candidate_profiles(filters, filters.daily_limit):
            if stop_event.is_set():
                break
            while not command_queue.empty():
                command = command_queue.get().strip().lower()
                if command == "status":
                    print_status(storage)
                elif command == "stop":
                    stop_event.set()
                    break

            if storage.daily_sent_count() >= filters.daily_limit:
                print("Дневной лимит достигнут.")
                break
            if storage.already_tested(profile.username):
                continue
            storage.upsert_new_profile(profile)

            message = message_template.replace("{{username}}", profile.username)
            client.send_message(profile, message)
            storage.mark_sent(profile.username, message)

            pause = random.randint(min_pause, max_pause)
            storage.add_event(profile.username, "pause", f"{pause}s")
            print(f"Пауза {pause} секунд. Команды: status, stop")
            wait_with_commands(pause, command_queue, stop_event)

        storage.add_event(None, "test_finished", "stopped" if stop_event.is_set() else "done")
        print_status(storage)
    finally:
        storage.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safe UI load-test console tool for an owned staging/mock environment."
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Путь к SQLite базе.")
    parser.add_argument("--target-url", help="URL собственного тестового стенда.")
    parser.add_argument("--no-dry-run", action="store_true", help="Пометить запуск как staging UI.")
    parser.add_argument("--min-pause", type=int, default=2, help="Минимальная пауза между действиями.")
    parser.add_argument("--max-pause", type=int, default=5, help="Максимальная пауза между действиями.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    dry_run = not args.no_dry_run
    if args.min_pause > args.max_pause:
        print("Ошибка: --min-pause не может быть больше --max-pause.")
        return 2

    try:
        ensure_safe_target(args.target_url)
        login, password, filters, message_template = collect_settings()
        if not confirm_settings(filters, message_template, dry_run, args.target_url):
            print("Запуск отменен.")
            return 0
        Path(args.db).parent.mkdir(parents=True, exist_ok=True) if Path(args.db).parent != Path(".") else None
        run_test(
            login=login,
            password=password,
            filters=filters,
            message_template=message_template,
            db_path=args.db,
            target_url=args.target_url,
            dry_run=dry_run,
            min_pause=args.min_pause,
            max_pause=args.max_pause,
        )
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 130
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
