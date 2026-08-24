import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
SID = cfg["google_sheet_id"]
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SID)
g = sh.worksheet("Groups")
GID = g.id

ids = g.col_values(1)
pos = {}
for i, v in enumerate(ids, start=1):
    v = str(v).strip()
    if v and v not in pos:
        pos[v] = i

def L(gid):
    r = pos.get(gid, 0)
    return "[#%s · строка %d](https://docs.google.com/spreadsheets/d/%s/edit#gid=%d&range=A%d)" % (gid, r, SID, GID, r)

UNK = [
    "Подписка Premium Plus",
    "Обработка отправления Drop-off (СЦ)",
    "Обработка отправления Drop-off (ПВЗ)",
    "Обработка отправления Drop-off партнёрами (ПВЗ)",
    "Грузоместо: Сортировка по составу",
    "Грузоместо: Корректировка состава",
    "Отгрузка в нерекомендованный слот",
    "Отгрузка в нерекомендованный слот - отмена начисления",
    "Превышение индекса ошибок: просроченная отгрузка",
    "Дополнительная обработка ОВХ",
    "Декомпенсации и возвращение товаров на склад",
    "Благотворительное пожертвование",
]
unk_line = ", ".join(n + " — 1 шт" for n in UNK)

T = """🧾 17.08 — что было важного

За сутки 2950 логов. Главное: склады дали залп в 1256 финальных отказов, а новый OOM-фатал убивает дозапись WB-отчётов без записи в `failed_jobs` — то есть без ретрая.

1️⃣ Склады: с 00:00 в `failed_jobs` 1256 `FetchFbsStocksJob` (часы 04–06, все одним `MaxAttemptsExceededException`) и 80 `SyncWarehousesJob` (часы 03–12, к 15:00 с 76 — ещё идут). Батч-логи дают аккаунты 9420 и 9477, из payload `account_id` не извлекается — радиус не измерен. Рядом встают синки: 159 transaction-синков дольше 6 минут, 126 очисток статуса watchdog, по orders 5 очисток у аккаунтов 9477 и 4658 (#dfcb157c).
→ Перезапустить сегодняшние failed jobs после стабилизации лимитов очереди; убрать параллельные запуски синка. %s

2️⃣ С 00:00 в `failed_jobs` 177 `FixProductTitlesJob` одним типом `MaxAttempts`: 93/71/13 в 05/06/07-й часы, после 07:00 новых нет. В логе `account_id=7278`, по payload аккаунты не извлеклись — радиус не измерен.
→ Поднять пропускную способность очереди и перезапустить джобы. %s

3️⃣ WB-отчёты не закрываются: за сутки 143 предупреждения `WeeklyInternalReport` (отчёт за 10–16.08 не готов, WB-часть отсутствует), 106 пустых отчётов при активности и 8 недель помечены непокрытыми после трёх пустых попыток — всплеск ×3.3 к среднему. Задето аккаунтов: 8, недели без финансовых данных.
→ Дозагрузить непокрытые недели и починить gate weekly-отчёта. %s

4️⃣ Новое: 3 фатала OOM 8 ГБ в `StoredJsonResponse.php:485` (дозапись WB-отчёта) в 10:45, 12:09 и 13:10 MSK, аллокация каждый раз 42 МБ — один и тот же отчёт. В `failed_jobs` с 00:00 таких 0: процесс умирает без отметки об отказе и ретрая. Радиус не измерен: fatal даёт `#0 {main}` без аккаунта и пути.
→ Перевести дозапись на потоковую, логировать `account_id`/путь. %s

5️⃣ В админской сверке файла (аккаунт 3879, 13:03) справочник типов Ozon ответил «Лимит запросов превышен», авто-вывод `type_id` отключился, 12 строк упали в «не разнесено». Задето аккаунтов: 1, транзакции не затронуты.
→ Добавить ретрай с backoff и кеш справочника на сутки. %s

🔁 Висит без изменений: closure после деплоя — 19 дней; Ozon transactions rate limit — 16 дней; Forte «ТЕСТ»-платёж — 20 дней.

💸 Неучтённые операции за сутки: %s. Разбивка: [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/%s/edit#gid=1830882857)

Со вчера: складской залп и `FixProductTitlesJob` рецидивировали; `Too many connections` и deadlock — 0 за сутки; новое — OOM и лимит справочника Ozon.

Фон: ~1398 логов в шумных группах. Не оценено: 0.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/%s/edit#gid=%d)""" % (
    L("c53da709"), L("09170105"), L("38212eaf"), L("a14bf0d0"),
    L("b405e930"), unk_line, SID, SID, GID)

print("ДЛИНА:", len(T))
if len(T) > 3900:
    print("ВНИМАНИЕ: превышен лимит, не записываю")
else:
    d = sh.worksheet("Digest")
    d.update(values=[["2026-08-17"], [T]], range_name="A1:A2", value_input_option="USER_ENTERED")
    print("Дайджест записан в Digest A1:A2")
print("-" * 70)
print(T)
