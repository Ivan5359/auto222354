# Telegram Safe Outreach Bot

Простой Telegram-бот для Railway.

Он работает в безопасном dry-run режиме:

- показывает тестовые аккаунты;
- запускает тестовую обработку;
- показывает статистику;
- умеет остановиться.

Реальный Instagram не используется.

## Как залить на GitHub

Загрузи в GitHub именно содержимое этой папки:

```text
github_ready_telegram_bot
```

В корне репозитория должны лежать:

```text
app.py
requirements.txt
Procfile
railway.json
start.sh
README.md
```

## Как запустить на Railway

1. Railway -> New Project.
2. Deploy from GitHub repo.
3. Выбери репозиторий.
4. Открой `Variables`.
5. Добавь:

```text
TELEGRAM_BOT_TOKEN
```

Туда вставь токен от `@BotFather`.

6. Добавь:

```text
PUBLIC_URL
```

Туда вставь ссылку Railway, например:

```text
https://your-project.up.railway.app
```

7. Нажми `Redeploy`.
8. Открой в браузере:

```text
https://your-project.up.railway.app/setup
```

9. Открой своего бота в Telegram и напиши:

```text
/start
```

## Кнопки

- `Аккаунты` - показать тестовые аккаунты.
- `Запустить` - начать dry-run.
- `Статистика` - показать результат.
- `Стоп` - остановить.
- `Помощь` - инструкция.
