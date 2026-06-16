# Архитектура сервиса

Документ описывает текущую архитектуру и требования к сервису. **Обязателен к актуализации при любом изменении проекта.**

## Назначение

Система мониторинга логов из Telegram. Бот читает сообщения из канала с логами ошибок (Zabbix / приложение `app.sellerdata.ru`), группирует и классифицирует их, складывает в Google Sheets, обогащает контекстом кода из Bitbucket и рекомендациями GPT, шлёт ежедневные сводки и **срочные уведомления по критичным логам**.

Это набор независимых Python-скриптов, запускаемых по cron внутри одного Docker-контейнера. Общего фреймворка/оркестратора нет — каждый скрипт самодостаточен, конфиг и доступы общие через `config.json` и Google Sheets.

## Поток данных

```
Telegram (канал с логами, chat_id)
        │  читают telethon-клиенты
        ▼
┌───────────────────────────────────────────────────────────┐
│ telegram_to_sheets.py  (*/30)                               │
│   • новые сообщения → лист "Original data"                  │
│   • нормализация + агрегация по шаблонам → лист "Groups"    │
│   • категоризация по листу "Categories"                     │
│   • чистка записей старше 30 дней                           │
└───────────────────────────────────────────────────────────┘
        │ Google Sheets (единый источник правды между скриптами)
        ▼
fetch_code_from_bitbucket.py (06:00) → колонка "Код из Bitbucket"
process_unhandled_errors.py  (06:10) → колонка "GPT-ответ", статус
unknown_transaction.py       (06:19) → лист "Unknown tx"
send_daily_summary.py        (06:20…) → отчёт в report_channel_id

alert_watcher.py (*/2) — отдельная быстрая ветка:
   Telegram → критичные триггеры из "Categories" → уведомление в alert_chat_id
   (НЕ пишет в Sheets, работает независимо от пайплайна выше)
```

**Ключевой принцип:** Google Sheets — это база данных и шина обмена между скриптами. `telegram_to_sheets.py` наполняет таблицу, остальные скрипты читают/дополняют её колонки. Прямой связи между скриптами (кроме общей таблицы) нет.

## Компоненты

| Скрипт | Cron | Лог | Что делает | Telethon | Sheets |
| --- | --- | --- | --- | --- | --- |
| `telegram_to_sheets.py` | `*/30` | `telegram_to_sheets.log` | Главный сборщик: сообщения → `Original data` + агрегация в `Groups` | да | rw |
| `alert_watcher.py` | `*/2` | `alert_watcher.log` | Срочные уведомления по критичным логам в `alert_chat_id` | да (сессия read-only) | r |
| `fetch_code_from_bitbucket.py` | `0 6` | `fetch_code_cron.log` | Подтягивает фрагменты кода по адресам ошибок из Bitbucket | нет | rw |
| `process_unhandled_errors.py` | `10 6` | `gpt_process.log` | GPT-анализ ошибок со статусом «не обработано» | нет | rw |
| `unknown_transaction.py` | `19 6` | `unknown_tx.log` | Лист `Unknown tx` по логам «Unknown transaction type» | нет | rw |
| `send_daily_summary.py` | `20,35,50 6` и `10,30,50 7` | `daily_summary.log` | Суточная сводка в Telegram (`report_channel_id`) | да | r |

`telegram_proxy.py` — общий хелпер: парсит прокси из `config.json`/env для всех telethon-клиентов.

Все времена cron — в часовом поясе `Europe/Moscow`.

## Листы Google Sheets

- **Original data** — сырые сообщения: `ID | Дата | Текст`. Хранятся 30 дней, потом чистятся.
- **Groups** — агрегированные ошибки по нормализованному шаблону: `Категория | Ошибка (шаблон) | Адреса | Код из Bitbucket | GPT-ответ | За 1 день | За 7 дней | За 30 дней | Последнее появление | Статус`. Сортируется по «За 1 день».
- **Categories** — правила категоризации, заполняется вручную: `Категория | Триггер | Алерт`.
  - `Триггер` — подстрока (lowercase), по которой лог относят к категории.
  - **`Алерт` (колонка C)** — любая непустая отметка делает триггер критичным: при появлении такого лога `alert_watcher.py` шлёт срочное уведомление.
- **Unknown tx** — отдельный разбор «Unknown transaction type» по платформам (WB/Ozon).

## Срочные уведомления (alert_watcher.py)

Логика троттлинга — самое важное при доработке:

- Запускается каждые 2 минуты, читает только сообщения новее своего курсора `alert_last_id.txt`.
- Совпавшие с критичными триггерами логи **копит** в `alert_state.json` (поле `pending`).
- Шлёт **одно сводное** сообщение в `alert_chat_id`, но **не чаще раза в 30 минут** (`ALERT_INTERVAL_MIN`, поле `last_sent`). Накопленное за окно уходит одним сообщением со списком **названий категорий**.
- Курсор двигается каждый запуск → каждое событие учитывается один раз.
- Настраиваемые параметры вверху файла: `ALERT_INTERVAL_MIN`, `MAX_LINES_PER_MESSAGE`, `MAX_PENDING`, `FETCH_LIMIT`.

## Конфигурация

`app/config.json` (НЕ в git, см. `.gitignore`):

```json
{
  "api_id": 0, "api_hash": "...", "session_name": "session",
  "chat_id": "-100...",            // канал, ОТКУДА читаем логи
  "google_sheet_id": "...",
  "bitbucket_username": "...", "bitbucket_app_password": "...",
  "bitbucket_repo": "owner/repo", "bitbucket_branch": "production",
  "openai_api_key": "sk-...",
  "report_channel_id": -000,       // куда шлём суточную сводку
  "alert_chat_id": -000            // КУДА шлём срочные уведомления
}
```

Прокси (опционально): `telegram_proxy` / `proxy` в config или env `TELEGRAM_PROXY`. Форматы — см. `telegram_proxy.py`.

## Инфраструктура

- **Сервер:** `bkp.sellerdata.ru`, пользователь `developer`, каталог проекта `/usr/local/other-scripts/telegram-to-sheets/`. Доступ по SSH-ключу `id_rsa`.
- **Контейнер:** один Docker-контейнер `telegram-to-sheets-app`, `network_mode: host`, TZ `Europe/Moscow`.
- **Volumes:** `./app → /app` (live-reload кода) и `./logs → /app/logs`.
- **Cron:** вшит в образ через `Dockerfile` (`COPY crontab /etc/cron.d/... && crontab ...`). `entrypoint.sh` стартует `cron` и держит контейнер живым через `tail -f` основного лога.

Подробности по развёртыванию и эксплуатации — в `README.md`.
