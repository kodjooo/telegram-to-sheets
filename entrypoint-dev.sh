#!/bin/bash

echo "=== РЕЖИМ РАЗРАБОТКИ ==="
echo "Изменения кода будут применяться автоматически без пересборки контейнера"

# Запуск cron демона (в режиме разработки тоже нужен для автоматических задач)
echo "Запуск cron демона..."
cron

# Вывод информации о часовом поясе
echo "Текущий часовой пояс: $(date)"
echo "Cron задания:"
crontab -l

# Создание первоначальных логов, если их нет
mkdir -p /app/logs
touch /app/logs/telegram_to_sheets.log
touch /app/logs/fetch_code_cron.log  
touch /app/logs/gpt_process.log
touch /app/logs/unknown_tx.log
touch /app/logs/daily_summary.log
touch /app/logs/dev.log

echo ""
echo "🚀 Приложение Telegram-to-Sheets запущено в режиме разработки!"
echo "📂 Код монтирован из: ./app/"
echo "📝 Логи сохраняются в: ./logs/"
echo ""
echo "💡 Полезные команды для разработки:"
echo "   - docker-compose -f docker-compose.dev.yml logs -f    # Просмотр логов"
echo "   - docker exec -it telegram-to-sheets-dev bash         # Вход в контейнер"
echo "   - docker-compose -f docker-compose.dev.yml restart    # Перезапуск без пересборки"
echo ""

# Функция для отслеживания изменений файлов Python
watch_files() {
    echo "👀 Отслеживание изменений Python файлов..."
    
    while true; do
        # Мониторим изменения в Python файлах
        inotifywait -r -e modify /app --include='.*\.py$' 2>/dev/null
        
        if [ $? -eq 0 ]; then
            echo "🔄 $(date '+%Y-%m-%d %H:%M:%S') - Обнаружены изменения в Python файлах" >> /app/logs/dev.log
            echo "🔄 $(date '+%Y-%m-%d %H:%M:%S') - Обнаружены изменения в Python файлах"
            
            # Опционально: можно добавить автоматический перезапуск главного скрипта
            # pkill -f telegram_to_sheets.py 2>/dev/null || true
        fi
        
        sleep 1
    done &
}

# Запуск мониторинга файлов если установлен inotify-tools
if command -v inotifywait >/dev/null 2>&1; then
    watch_files
else
    echo "⚠️  inotify-tools не установлен - автоматическое отслеживание изменений недоступно"
    echo "   Для включения установите: apt-get install inotify-tools"
fi

echo "📋 Показать активные cron задания:"
crontab -l

echo ""
echo "📊 Мониторинг логов (Ctrl+C для остановки):"

# Мониторинг нескольких логов одновременно в режиме разработки
tail -f /app/logs/telegram_to_sheets.log /app/logs/dev.log 2>/dev/null &

# Бесконечное ожидание чтобы контейнер продолжал работать
wait
