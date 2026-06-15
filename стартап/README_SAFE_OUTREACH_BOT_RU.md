# Safe Outreach UI Bot

Это безопасный бот для нагрузочного тестирования собственного mock/staging UI.
Он проверяет сценарий: открыть тестовый профиль, нажать кнопку сообщения,
ввести текст, отправить, записать результат в SQLite.

Бот не предназначен для Instagram и не запускается против `instagram.com`.

## Быстрый старт

1. Установить зависимости:

```powershell
pip install playwright
python -m playwright install chromium
```

2. Создать конфиг:

```powershell
python safe_outreach_bot.py --init-config
```

3. Запустить в безопасном dry-run режиме:

```powershell
python safe_outreach_bot.py
```

## Режимы

Dry-run ничего не открывает в браузере и просто имитирует действия:

```powershell
python safe_outreach_bot.py --dry-run
```

Запуск против вашего staging UI:

```powershell
python safe_outreach_bot.py --live-staging
```

## Ожидаемые элементы на тестовом сайте

По умолчанию бот ищет такие элементы:

```html
[data-testid="login-username"]
[data-testid="login-password"]
[data-testid="login-submit"]
[data-testid="message-button"]
[data-testid="message-input"]
[data-testid="send-message"]
```

Если на стенде другие селекторы, поменяйте их в `safe_outreach_config.json`
в блоке `selectors`.

## Команды во время работы

Вводятся прямо в консоль:

- `status` — показать статистику.
- `pause` — поставить тест на паузу.
- `resume` — продолжить.
- `stop` — аккуратно остановить.
- `export leads.csv` — выгрузить базу лидов в CSV.

## CSV

Импорт:

```powershell
python safe_outreach_bot.py --import-csv leads.csv
```

CSV должен содержать колонки:

```csv
username,followers,has_site,niche
test_shop_001,1200,true,bags
test_shop_002,850,false,jewelry
```

Экспорт:

```powershell
python safe_outreach_bot.py --export-csv leads_export.csv
```

Статистика:

```powershell
python safe_outreach_bot.py --stats
```

## Главные настройки

Все основные параметры лежат в `safe_outreach_config.json`:

- `target_url` — адрес вашего тестового стенда.
- `dry_run` — безопасная имитация без браузера.
- `daily_limit` — дневной лимит действий.
- `pause_min_seconds` и `pause_max_seconds` — паузы между действиями.
- `niche_keywords` — тестовые ниши для генерации демо-лидов.
- `followers_min` и `followers_max` — фильтр по числу подписчиков.
- `site_filter` — `yes`, `no` или `any`.
- `message_template` — шаблон сообщения, поддерживает `{{username}}`.

## База данных

По умолчанию используется SQLite-файл:

```text
safe_outreach.sqlite3
```

В таблице `leads` хранятся лиды и статусы:

- `new`
- `sent`
- `replied`
- `rejected`

В таблице `events` хранится журнал действий.
