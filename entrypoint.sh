#!/bin/bash

# Запуск cron демона
echo "Запуск cron демона..."
cron

# Вывод информации о часовом поясе
echo "Текущий часовой пояс: $(date)"
echo "Cron задания:"
crontab -l

# Создание первоначальных логов, если их нет
touch /app/logs/telegram_to_sheets.log
touch /app/logs/fetch_code_cron.log  
touch /app/logs/gpt_process.log
touch /app/logs/unknown_tx.log
touch /app/logs/daily_summary.log

echo "Приложение Telegram-to-Sheets запущено!"
echo "Логи будут записываться в /app/logs/"

# Показать текущие cron задания
echo "Активные cron задания:"
crontab -l

# Бесконечное ожидание чтобы контейнер продолжал работать
tail -f /app/logs/telegram_to_sheets.log
