# Telegram to Google Sheets — Анализатор логов ошибок

Автоматизированная система для обработки логов ошибок из Telegram канала и их анализа с последующей записью в Google Sheets. Система включает в себя получение контекста кода из Bitbucket, анализ ошибок через GPT и отправку ежедневных отчетов.

## Быстрый старт

```bash
# Клонирование репозитория
git clone https://github.com/kodjooo/telegram-to-sheets.git
cd telegram-to-sheets

# Настройка конфигурации (создайте файлы с вашими данными)
cp app/config.json.example app/config.json  # Отредактируйте под ваши API ключи
cp app/google-credentials.json.example app/google-credentials.json  # Добавьте ваши Google credentials

# Запуск через Docker
docker-compose up -d --build

# Проверка логов
docker-compose logs -f
```

> **Важно:** Перед запуском убедитесь, что у вас настроены все необходимые API ключи в файле `config.json`.

## Функциональность

### Основные компоненты:

1. **telegram_to_sheets.py** — главный модуль
   - Читает сообщения из Telegram канала через API
   - Группирует и классифицирует ошибки по шаблонам
   - Записывает данные в Google Sheets (листы "Original data" и "Groups")
   - Удаляет устаревшие данные (старше 30 дней)

2. **fetch_code_from_bitbucket.py** — получение контекста кода
   - Анализирует адреса ошибок из таблицы
   - Загружает фрагменты кода из Bitbucket API (40 строк вокруг ошибки)
   - Записывает код обратно в таблицу для последующего анализа

3. **process_unhandled_errors.py** — анализ через GPT
   - Обрабатывает ошибки со статусом "не обработано"
   - Отправляет ошибки и контекст кода в OpenAI GPT
   - Получает рекомендации по исправлению и записывает в таблицу

4. **send_daily_summary.py** — ежедневные отчеты
   - Подсчитывает статистику ошибок по категориям за день
   - Отправляет сводку в отдельный Telegram канал

5. **unknown_transaction.py** — специализированный анализатор
   - Анализирует логи с "Unknown transaction type"
   - Группирует по платформам (Wildberries/Ozon)
   - Создает отдельный лист "Unknown tx" в таблице

## Требования

### API ключи и доступы:
- **Telegram API**: api_id, api_hash (получить на https://my.telegram.org)
- **Google Sheets API**: service account credentials
- **Bitbucket API**: username и app password
- **OpenAI API**: ключ для GPT-4

### Структура конфигурации (`config.json`):
```json
{
  "api_id": "ваш_telegram_api_id",
  "api_hash": "ваш_telegram_api_hash", 
  "session_name": "session",
  "chat_id": "id_канала_с_логами",
  "google_sheet_id": "id_google_таблицы",
  "bitbucket_username": "username",
  "bitbucket_app_password": "app_password",
  "bitbucket_repo": "owner/repo_name",
  "bitbucket_branch": "production",
  "openai_api_key": "sk-...",
  "report_channel_id": "id_канала_для_отчетов"
}
```

## Развертывание

### Создание репозитория на GitHub

1. **Создайте новый репозиторий на GitHub:**
   - Перейдите на https://github.com/new
   - Назовите репозиторий `telegram-to-sheets`
   - Добавьте описание: "Анализатор логов ошибок из Telegram с записью в Google Sheets"
   - Оставьте репозиторий публичным или сделайте приватным по необходимости
   - Нажмите "Create repository"

2. **Загрузите код в репозиторий:**
   ```bash
   # Если у вас уже есть локальная копия проекта
   git remote add origin https://github.com/kodjooo/telegram-to-sheets.git
   git branch -M main
   git add .
   git commit -m "Initial commit: Telegram to Google Sheets analyzer"
   git push -u origin main
   ```

### Локальное развертывание

1. **Клонируйте репозиторий:**
   ```bash
   git clone https://github.com/kodjooo/telegram-to-sheets.git
   cd telegram-to-sheets
   ```
   
   > **Примечание:** Также можно использовать SSH для клонирования:
   > ```bash
   > git clone git@github.com:kodjooo/telegram-to-sheets.git
   > ```

2. **Настройте конфигурацию:**
   - Поместите ваш `config.json` в папку `app/`
   - Поместите `google-credentials.json` в папку `app/`
   - Если есть сессия Telegram, поместите `session.session` в папку `app/`

3. **Соберите и запустите Docker контейнер:**
   ```bash
   docker-compose up -d --build
   ```

4. **Проверьте статус:**
   ```bash
   docker-compose logs -f telegram-to-sheets
   ```

### Развертывание на удаленном сервере

1. **Подготовьте сервер:**
   ```bash
   # Установите Docker и Docker Compose
   curl -fsSL https://get.docker.com -o get-docker.sh
   sh get-docker.sh
   sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
   ```

2. **Клонируйте репозиторий на сервер:**
   ```bash
   ssh user@server
   git clone https://github.com/kodjooo/telegram-to-sheets.git
   cd telegram-to-sheets
   ```
   
   Или если проект уже существует локально:
   ```bash
   scp -r telegram-to-sheets/ user@server:/home/user/
   ```

3. **Запустите на сервере:**
   ```bash
   ssh user@server
   cd telegram-to-sheets
   docker-compose up -d --build
   ```

4. **Настройте автозапуск при перезагрузке сервера:**
   ```bash
   sudo crontab -e
   # Добавьте строку:
   @reboot cd /home/user/telegram-to-sheets && docker-compose up -d
   ```

## Расписание автоматических задач

Система использует cron для автоматического выполнения задач:

- **Каждые 30 минут**: Обработка новых сообщений из Telegram
- **06:00 ежедневно**: Загрузка контекста кода из Bitbucket  
- **06:10 ежедневно**: Обработка ошибок через GPT
- **06:19 ежедневно**: Анализ неизвестных транзакций
- **06:20 ежедневно**: Отправка ежедневного отчета

Все времена указаны в часовом поясе Europe/Moscow.

## Мониторинг и логи

### Просмотр логов:
```bash
# Все логи контейнера
docker-compose logs -f

# Логи конкретных модулей
docker exec telegram-to-sheets-app tail -f /app/logs/telegram_to_sheets.log
docker exec telegram-to-sheets-app tail -f /app/logs/gpt_process.log
docker exec telegram-to-sheets-app tail -f /app/logs/daily_summary.log
```

### Отладка:
```bash
# Войти в контейнер
docker exec -it telegram-to-sheets-app bash

# Проверить cron задания
crontab -l

# Ручной запуск скрипта
cd /app && python telegram_to_sheets.py
```

## Управление контейнером

```bash
# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Перезапуск
docker-compose restart

# Пересборка после изменений
docker-compose up -d --build

# Обновление образа
docker-compose pull && docker-compose up -d
```

## Структура проекта

```
telegram-to-sheets/
├── app/                          # Функциональные файлы приложения
│   ├── telegram_to_sheets.py     # Основной модуль
│   ├── fetch_code_from_bitbucket.py
│   ├── process_unhandled_errors.py
│   ├── send_daily_summary.py
│   ├── unknown_transaction.py
│   ├── config.json               # Конфигурация (создать самостоятельно)
│   ├── google-credentials.json   # Ключи Google API (создать самостоятельно)
│   ├── session.session           # Сессия Telegram (создается автоматически)
│   └── last_message_id.txt       # Последний обработанный ID
├── logs/                         # Логи приложения (создается автоматически)
├── Dockerfile                    # Конфигурация Docker образа
├── docker-compose.yml            # Конфигурация Docker Compose
├── requirements.txt              # Python зависимости
├── crontab                       # Расписание cron задач
├── entrypoint.sh                 # Скрипт запуска контейнера
└── README.md                     # Данная документация
```

## Устранение неполадок

### Проблемы с Telegram API:
- Убедитесь, что session.session файл доступен для записи
- Проверьте правильность api_id и api_hash
- При первом запуске может потребоваться авторизация

### Проблемы с Google Sheets:
- Убедитесь, что service account имеет доступ к таблице
- Проверьте правильность google_sheet_id в конфигурации

### Проблемы с cron:
- Проверьте часовой пояс: `docker exec telegram-to-sheets-app date`
- Посмотрите активные задания: `docker exec telegram-to-sheets-app crontab -l`

### Общие проблемы:
- Проверьте доступность внешних API
- Убедитесь, что все файлы конфигурации на месте
- Проверьте логи на наличие ошибок

## Безопасность

- Все API ключи хранятся в конфигурационных файлах внутри контейнера
- Рекомендуется использовать Docker secrets в продакшене
- Логи не содержат чувствительной информации
- Регулярно обновляйте зависимости Python

## Поддержка

При возникновении проблем:
1. Проверьте логи контейнера
2. Убедитесь в правильности конфигурации
3. Проверьте доступность внешних сервисов
4. При необходимости пересоберите контейнер с флагом --no-cache
