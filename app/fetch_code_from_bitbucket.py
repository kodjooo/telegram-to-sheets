import os
import json
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Пути и настройки
BASE_DIR = '/app'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')

# Загрузка конфигурации
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

BITBUCKET_USERNAME = config['bitbucket_username']
BITBUCKET_APP_PASSWORD = config['bitbucket_app_password']
BITBUCKET_REPO = config['bitbucket_repo']  # например, 'sellerdata/app'
BITBUCKET_BRANCH = config.get('bitbucket_branch', 'master')

# Авторизация в Google Sheets
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(config['google_sheet_id'])
sheet = spreadsheet.worksheet('Groups')

# Получаем все строки
rows = sheet.get_all_values()
header = rows[0]
rows_data = rows[1:]

# Индексы столбцов
try:
    address_idx = header.index("Адреса")
    code_idx = header.index("Код из Bitbucket")
except ValueError as e:
    raise Exception(f"Не найден нужный столбец: {e}")

# Обработка строк
for i, row in enumerate(rows_data, start=2):
    if len(row) <= address_idx:
        continue

    address = row[address_idx].strip()
    if not address.startswith('app/') or ':' not in address:
        continue

    if len(row) > code_idx and row[code_idx].strip():
        # Уже есть код, пропускаем
        continue

    path, line_str = address.split(':')
    try:
        line_num = int(line_str)
    except ValueError:
        continue

    # Запрос к Bitbucket
    url = f"https://api.bitbucket.org/2.0/repositories/{BITBUCKET_REPO}/src/{BITBUCKET_BRANCH}/{path}"
    response = requests.get(url, auth=(BITBUCKET_USERNAME, BITBUCKET_APP_PASSWORD))

    if response.status_code != 200:
        print(f"Ошибка {response.status_code} при получении {path}")
        continue

    content = response.text.splitlines()
    start = max(0, line_num - 21)
    end = min(len(content), line_num + 20)
    code_snippet = '\n'.join(content[start:end])

    # Запись в таблицу
    sheet.update_cell(i, code_idx + 1, code_snippet)

print("Завершено")