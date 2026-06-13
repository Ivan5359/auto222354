# Telegram Outreach Bot for Railway

## Что это

Telegram-бот для полуавтоматического outreach:

- хранит лиды;
- фильтрует по подписчикам, нише и наличию сайта;
- выдаёт следующий аккаунт;
- даёт кнопку открыть Instagram;
- даёт готовый текст;
- хранит, кому уже писали;
- показывает follow-up;
- держит дневной лимит.

Бот не логинится в Instagram и не отправляет Direct автоматически.

## Railway

1. Загрузи эту папку в GitHub.
2. В Railway создай новый проект из GitHub.
3. В Variables добавь:

```text
TELEGRAM_BOT_TOKEN=твой_токен_от_BotFather
```

4. Если хочешь хранить базу на Railway Volume, добавь volume и поставь переменную:

```text
OUTREACH_BOT_DB=/data/outreach_bot.sqlite3
```

Без volume база может сбрасываться при redeploy.

5. Railway сам запустит:

```text
python telegram_outreach_bot.py
```

через `Procfile`.

## Команды в Telegram

```text
/start
/add
/next
/filter
/filter min=500 max=5000 site=no_site niche=сумки
/template
/followup_template
/followups
/limit 20
/stats
/reset_skipped
```

## Формат лидов

```text
@brand.ua | 1200 | сумки | no-site | только Instagram
@jewelry.ua | 3400 | украшения | has-site | сайт слабый
```
