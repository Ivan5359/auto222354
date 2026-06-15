# Telegram-only бот без сайта

Это обычный Telegram-бот без HTML-сайта.

## Что загрузить в GitHub

Загрузи содержимое этой папки:

```text
telegram_only_ready
```

В GitHub в корне должны лежать:

```text
telegram_bot.py
requirements.txt
Procfile
railway.json
start.sh
runtime.txt
nixpacks.toml
README.md
```

## Railway

1. Railway -> New Project.
2. Deploy from GitHub repo.
3. Выбери репозиторий.
4. Открой `Variables`.
5. Добавь:

```text
TELEGRAM_BOT_TOKEN
```

6. Вставь токен от `@BotFather`.
7. Нажми `Redeploy`.

`PUBLIC_URL` не нужен.

## Telegram

Открой своего бота и напиши:

```text
/start
```

## Команды

```text
/start
/accounts
/run
/status
/stop
/reset
/help
```
