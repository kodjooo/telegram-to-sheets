#!/bin/bash

# Скрипт для удобного управления разработкой

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_help() {
    echo -e "${BLUE}🛠️  Telegram-to-Sheets Development Helper${NC}"
    echo ""
    echo "Использование: ./dev.sh [команда]"
    echo ""
    echo "Команды:"
    echo "  start     - Запуск в режиме разработки"
    echo "  stop      - Остановка контейнера разработки"
    echo "  restart   - Перезапуск без пересборки"
    echo "  rebuild   - Пересборка и запуск"
    echo "  logs      - Просмотр логов"
    echo "  shell     - Вход в контейнер"
    echo "  status    - Статус контейнера"
    echo "  clean     - Очистка и остановка"
    echo "  prod      - Переключение на production режим"
    echo ""
}

case "$1" in
    "start")
        echo -e "${GREEN}🚀 Запуск в режиме разработки...${NC}"
        docker-compose -f docker-compose.dev.yml up -d --build
        echo ""
        echo -e "${YELLOW}💡 Полезная информация:${NC}"
        echo "  - Код автоматически обновляется при изменениях"
        echo "  - Логи: ./dev.sh logs"
        echo "  - Вход в контейнер: ./dev.sh shell"
        echo "  - Остановка: ./dev.sh stop"
        ;;
    "stop")
        echo -e "${YELLOW}⏹️  Остановка контейнера разработки...${NC}"
        docker-compose -f docker-compose.dev.yml down
        ;;
    "restart")
        echo -e "${BLUE}🔄 Перезапуск без пересборки...${NC}"
        docker-compose -f docker-compose.dev.yml restart
        ;;
    "rebuild")
        echo -e "${BLUE}🔧 Пересборка и запуск...${NC}"
        docker-compose -f docker-compose.dev.yml down
        docker-compose -f docker-compose.dev.yml up -d --build
        ;;
    "logs")
        echo -e "${GREEN}📋 Просмотр логов (Ctrl+C для выхода)...${NC}"
        docker-compose -f docker-compose.dev.yml logs -f
        ;;
    "shell")
        echo -e "${GREEN}🐚 Вход в контейнер...${NC}"
        docker exec -it telegram-to-sheets-dev bash
        ;;
    "status")
        echo -e "${BLUE}📊 Статус контейнера:${NC}"
        docker-compose -f docker-compose.dev.yml ps
        echo ""
        echo -e "${BLUE}📈 Использование ресурсов:${NC}"
        docker stats telegram-to-sheets-dev --no-stream 2>/dev/null || echo "Контейнер не запущен"
        ;;
    "clean")
        echo -e "${RED}🧹 Очистка контейнеров и образов...${NC}"
        docker-compose -f docker-compose.dev.yml down --rmi all --volumes
        ;;
    "prod")
        echo -e "${GREEN}🏭 Переключение на production режим...${NC}"
        docker-compose -f docker-compose.dev.yml down
        docker-compose up -d --build
        echo "Production контейнер запущен!"
        ;;
    "")
        print_help
        ;;
    *)
        echo -e "${RED}❌ Неизвестная команда: $1${NC}"
        echo ""
        print_help
        exit 1
        ;;
esac
