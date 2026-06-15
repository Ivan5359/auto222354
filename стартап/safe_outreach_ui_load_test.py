#!/usr/bin/env python3
"""
Безопасная исправленная версия UI load-test сценария.

Скрипт предназначен для собственного mock/staging интерфейса, а не для
instagram.com. Он сохраняет идею теста: логин, сбор тестовых лидов,
дедупликация, отправка сообщения через UI, SQLite-логирование и команды
status/stop во время выполнения.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright
from sqlalchemy import Boolean, Column, DateTime, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = "sqlite+aiosqlite:///outreach_test.db"
DEFAULT_DAILY_LIMIT = 20
FORBIDDEN_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("UI-Load-Test")

Base = declarative_base()


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    followers = Column(Integer, nullable=False)
    has_site = Column(Boolean, default=False)
    niche = Column(String, nullable=False)
    status = Column(String, default="new")
    last_contacted = Column(DateTime, nullable=True)
    follow_up_date = Column(DateTime, nullable=True)
    message_sent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@dataclass(frozen=True)
class Filters:
    niche_keywords: list[str]
    followers_from: int
    followers_to: int
    site_filter: Literal["yes", "no", "any"]
    daily_limit: int


@dataclass(frozen=True)
class TestLead:
    username: str
    followers: int
    has_site: bool
    niche: str


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def validate_target_url(target_url: str) -> None:
    host = (urlparse(target_url).hostname or "").lower()
    if host in FORBIDDEN_HOSTS or host.endswith(".instagram.com"):
        raise ValueError("Этот тестовый инструмент не запускается против Instagram.")


def ask_int(prompt: str, default: int, min_value: int = 0) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Введите целое число.")
            continue
        if value >= min_value:
            return value
        print(f"Значение должно быть не меньше {min_value}.")


def ask_site_filter() -> Literal["yes", "no", "any"]:
    while True:
        raw = input("Наличие сайта: yes/no/any [any]: ").strip().lower()
        if not raw:
            return "any"
        if raw in {"yes", "no", "any"}:
            return raw  # type: ignore[return-value]
        print("Введите yes, no или any.")


def collect_settings() -> tuple[str, str, str, Filters, str]:
    print("=== Настройка UI load-test ===")
    target_url = input("URL вашего тестового стенда: ").strip()
    validate_target_url(target_url)

    username = input("Логин тестового аккаунта: ").strip()
    password = getpass.getpass("Пароль тестового аккаунта: ")

    raw_keywords = input("Ниша, ключевые слова через запятую: ").strip()
    keywords = [item.strip().lower() for item in raw_keywords.split(",") if item.strip()]
    if not keywords:
        keywords = ["bags", "clothes", "jewelry"]

    followers_from = ask_int("Подписчиков от", 100, 0)
    followers_to = ask_int("Подписчиков до", 50_000, followers_from)
    site_filter = ask_site_filter()
    daily_limit = ask_int("Максимум профилей за день", DEFAULT_DAILY_LIMIT, 1)

    message_template = input("Шаблон сообщения, можно {{username}}: ").strip()
    if not message_template:
        message_template = "Здравствуйте, {{username}}! Это тестовое сообщение стенда."

    filters = Filters(
        niche_keywords=keywords,
        followers_from=followers_from,
        followers_to=followers_to,
        site_filter=site_filter,
        daily_limit=daily_limit,
    )
    return target_url, username, password, filters, message_template


def lead_matches(lead: TestLead, filters: Filters) -> bool:
    if not filters.followers_from <= lead.followers <= filters.followers_to:
        return False
    if filters.site_filter == "yes" and not lead.has_site:
        return False
    if filters.site_filter == "no" and lead.has_site:
        return False
    return True


def generate_test_leads(filters: Filters) -> list[TestLead]:
    leads: list[TestLead] = []
    for index in range(filters.daily_limit * 5):
        niche = random.choice(filters.niche_keywords)
        candidate = TestLead(
            username=f"test_{niche}_{index:04d}".replace(" ", "_"),
            followers=random.randint(
                max(0, filters.followers_from - 500), filters.followers_to + 5_000
            ),
            has_site=random.choice([True, False]),
            niche=niche,
        )
        if lead_matches(candidate, filters):
            leads.append(candidate)
        if len(leads) >= filters.daily_limit:
            break
    return leads


async def already_processed(username: str) -> bool:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.username == username))
        lead = result.scalar_one_or_none()
        return lead is not None and lead.status in {"sent", "replied", "rejected"}


async def save_lead(lead: TestLead, status: str, message: str | None = None) -> None:
    async with async_session() as session:
        existing_result = await session.execute(select(Lead).where(Lead.username == lead.username))
        existing = existing_result.scalar_one_or_none()
        if existing:
            existing.status = status
            existing.last_contacted = datetime.now() if status == "sent" else existing.last_contacted
            existing.message_sent = message or existing.message_sent
        else:
            session.add(
                Lead(
                    username=lead.username,
                    followers=lead.followers,
                    has_site=lead.has_site,
                    niche=lead.niche,
                    status=status,
                    last_contacted=datetime.now() if status == "sent" else None,
                    message_sent=message,
                )
            )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def print_status() -> None:
    async with async_session() as session:
        result = await session.execute(select(Lead.status))
        statuses = [row[0] for row in result.all()]
    counts = {name: statuses.count(name) for name in ["new", "sent", "replied", "rejected"]}
    print(
        "Статистика: "
        f"new={counts['new']}, sent={counts['sent']}, "
        f"replied={counts['replied']}, rejected={counts['rejected']}"
    )


async def login_test_app(page: Page, target_url: str, username: str, password: str) -> None:
    await page.goto(target_url, wait_until="domcontentloaded")

    username_input = page.locator("[data-testid='login-username']")
    password_input = page.locator("[data-testid='login-password']")
    submit_button = page.locator("[data-testid='login-submit']")

    if await username_input.count() == 0:
        logger.info("Форма логина не найдена: считаем, что стенд уже авторизован.")
        return

    await username_input.fill(username)
    await password_input.fill(password)
    await submit_button.click()
    await page.wait_for_load_state("networkidle")


async def send_message(page: Page, target_url: str, lead: TestLead, message: str) -> bool:
    profile_url = f"{target_url.rstrip('/')}/profiles/{lead.username}"
    await page.goto(profile_url, wait_until="domcontentloaded")

    message_button = page.locator("[data-testid='message-button']")
    message_input = page.locator("[data-testid='message-input']")
    send_button = page.locator("[data-testid='send-message']")

    if await message_button.count() == 0:
        logger.warning("На профиле @%s нет кнопки сообщения", lead.username)
        return False

    await message_button.click()
    await message_input.fill(message)
    await send_button.click()
    await page.wait_for_timeout(500)
    return True


async def command_watcher(queue: asyncio.Queue[str]) -> None:
    while True:
        command = await asyncio.to_thread(input)
        await queue.put(command.strip().lower())


async def pause_with_commands(seconds: float, queue: asyncio.Queue[str]) -> bool:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        timeout = min(1.0, deadline - asyncio.get_running_loop().time())
        try:
            command = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            continue

        if command == "status":
            await print_status()
        elif command == "stop":
            return False
    return True


async def run() -> None:
    await init_db()
    target_url, username, password, filters, message_template = collect_settings()
    leads = generate_test_leads(filters)

    print("\n=== Подтверждение ===")
    print(f"Стенд: {target_url}")
    print(f"Лидов к тесту: {len(leads)}")
    print(f"Лимит: {filters.daily_limit}")
    print("Команды во время выполнения: status, stop")
    if input("Запустить? [y/N]: ").strip().lower() != "y":
        print("Отменено.")
        return

    command_queue: asyncio.Queue[str] = asyncio.Queue()
    command_task = asyncio.create_task(command_watcher(command_queue))

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            page = await browser.new_page()
            await login_test_app(page, target_url, username, password)

            sent = 0
            for lead in leads:
                while not command_queue.empty():
                    command = await command_queue.get()
                    if command == "status":
                        await print_status()
                    elif command == "stop":
                        print("Остановлено пользователем.")
                        return

                if sent >= filters.daily_limit:
                    print("Дневной лимит достигнут.")
                    break
                if await already_processed(lead.username):
                    continue

                await save_lead(lead, "new")
                message = message_template.replace("{{username}}", lead.username)
                ok = await send_message(page, target_url, lead, message)
                if ok:
                    sent += 1
                    await save_lead(lead, "sent", message)
                    print(f"Отправлено {sent}/{filters.daily_limit}: @{lead.username}")
                else:
                    await save_lead(lead, "rejected")
                    print(f"Не отправлено: @{lead.username}")

                delay = random.uniform(2, 5)
                print(f"Пауза {delay:.1f} сек. Команды: status, stop")
                should_continue = await pause_with_commands(delay, command_queue)
                if not should_continue:
                    print("Остановлено пользователем.")
                    break

            await browser.close()
            await print_status()
    finally:
        command_task.cancel()


if __name__ == "__main__":
    asyncio.run(run())
