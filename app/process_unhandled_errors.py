import os
import json
from openai import OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Пути
BASE_DIR = '/app'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'google-credentials.json')

# Загрузка конфигурации
with open(CONFIG_PATH, 'r') as f:
    config = json.load(f)

# Настройка OpenAI клиента
client_ai = OpenAI(api_key=config['openai_api_key'])

# Авторизация в Google Sheets
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client_gs = gspread.authorize(creds)
spreadsheet = client_gs.open_by_key(config['google_sheet_id'])
sheet = spreadsheet.worksheet('Groups')

# Получаем данные
rows = sheet.get_all_values()
header = rows[0]
rows_data = rows[1:]

# Индексы нужных столбцов
error_idx = header.index("Ошибка (шаблон)")
code_idx = header.index("Код из Bitbucket")  # исправлено здесь
gpt_idx = header.index("GPT-ответ")
status_idx = header.index("Статус")

# Обработка строк со статусом "не обработано"
for i, row in enumerate(rows_data, start=2):
    if len(row) <= status_idx or row[status_idx].strip().lower() != 'не обработано':
        continue

    error_text = row[error_idx].strip()
    code_context = row[code_idx].strip() if len(row) > code_idx else ""

    if code_context:
        prompt = (
            f"Найди и исправь ошибку в следующем коде. "
            f"Это фрагмент, связанный с ошибкой:\n\n"
            f"Ошибка:\n{error_text}\n\n"
            f"Контекст (строка ошибки и 20 строк до и после неё):\n{code_context}\n\n"
            f"Опиши, как исправить ошибку — покажи исправленный код или укажи, если дело не в коде."
        )
    else:
        prompt = (
            f"Ошибка:\n{error_text}\n\n"
            f"Объясни, как её исправить. "
            f"Если это связано с кодом — что нужно сделать, если это связано с данными или окружением — тоже поясни."
        )

    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o",  # можешь заменить на "gpt-4o-mini", если нужно
            messages=[
                {"role": "system", "content": "Ты технический помощник. Твоя задача — находить причину ошибки и давать чёткое решение."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        answer = response.choices[0].message.content
        sheet.update_cell(i, gpt_idx + 1, answer)
        sheet.update_cell(i, status_idx + 1, "обработано")
        print(f"✅ Строка {i} обработана.")
    except Exception as e:
        print(f"❌ Ошибка в строке {i}: {e}")

print("Завершено.")