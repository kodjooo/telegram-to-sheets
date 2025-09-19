import os
import json
import gspread
from collections import defaultdict
from oauth2client.service_account import ServiceAccountCredentials
from telethon import TelegramClient
import asyncio
from datetime import datetime

# Пути
BASE_DIR = '/app'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')

# Загрузка конфигурации
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

# Авторизация в Google Sheets
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client_gs = gspread.authorize(creds)
spreadsheet = client_gs.open_by_key(config['google_sheet_id'])
sheet = spreadsheet.worksheet('Groups')

# Считаем количество ошибок по категориям
rows = sheet.get_all_values()
if not rows or len(rows) < 2:
    message = "⚠️ Нет данных для формирования отчета."
else:
    header = rows[0]
    category_idx = header.index("Категория")
    count_1d_idx = header.index("За 1 день")

    category_counts = defaultdict(int)
    empty_category_count = 0

    for row in rows[1:]:
        try:
            count = int(row[count_1d_idx])
        except:
            continue

        category = row[category_idx].strip() if len(row) > category_idx else ''
        if category:
            category_counts[category] += count
        else:
            empty_category_count += count

    total = sum(category_counts.values()) + empty_category_count
    date_str = datetime.now().strftime('%d.%m.%Y')

    # Формирование текста отчета
    message_lines = [f"🧾 Ежедневная сводка за {date_str} ({total} всего):"]
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        message_lines.append(f"• {cat}: {count}")
    if empty_category_count:
        message_lines.append(f"• Без категории: {empty_category_count}")

    # Ссылка на таблицу
    message_lines.append("\n👉 [Подробнее](https://docs.google.com/spreadsheets/d/1eSuLIAlnxkZHA4jy2cBZVwWiA__NZ3pl5hncxU7O3RU/edit?gid=807594473)")

    message = "\n".join(message_lines)

# Отправка в Telegram
async def send_message():
    session_file = os.path.join(BASE_DIR, config['session_name'])
    client = TelegramClient(session_file, config['api_id'], config['api_hash'])
    await client.start()
    await client.send_message(
        config['report_channel_id'],
        message,
        parse_mode='markdown',
        link_preview=False  # отключает предпросмотр ссылки
    )
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(send_message())