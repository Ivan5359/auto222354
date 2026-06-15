# Как запустить на Railway

Самый быстрый вариант:

1. Создай новый репозиторий на GitHub.
2. Загрузи туда всю папку проекта.
3. Открой Railway.
4. Нажми `New Project`.
5. Выбери `Deploy from GitHub repo`.
6. Выбери этот репозиторий.
7. Railway сам увидит `requirements.txt` и запустит приложение.

После деплоя открой ссылку Railway. Там будет простая веб-панель:

- настройка лимита;
- настройка ниш;
- шаблон сообщения;
- dry-run запуск;
- статистика;
- кнопка остановки;
- таблица последних тестовых лидов.

## Важно

Приложение работает в безопасном режиме dry-run. Оно не подключается к Instagram
и не делает реальную рассылку.

## Локальный запуск без PowerShell

Можно запустить из любого терминала:

```bash
python web_safe_outreach.py
```

Потом открыть:

```text
http://localhost:8080
```

## Что загружать в GitHub

Минимально нужны эти файлы:

```text
web_safe_outreach.py
safe_outreach_bot.py
requirements.txt
Procfile
railway.json
README_DEPLOY_RAILWAY_RU.md
```
