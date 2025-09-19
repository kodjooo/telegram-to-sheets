# Используем Python 3.11 slim образ
FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    cron \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копирование файла зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование файлов приложения
COPY app/ .

# Создание папки для логов
RUN mkdir -p /app/logs

# Установка переменной окружения для временной зоны
ENV TZ=Europe/Moscow

# Копирование crontab файла и установка cron заданий
COPY crontab /etc/cron.d/telegram-app-cron
RUN chmod 0644 /etc/cron.d/telegram-app-cron && \
    crontab /etc/cron.d/telegram-app-cron

# Создание точки входа с запуском cron
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Установка точки входа
ENTRYPOINT ["/entrypoint.sh"]
