import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
SID = cfg["google_sheet_id"]
GID = 1412410715

def L(gid, row):
    return f"[#{gid} · строка {row}](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={GID}&range=A{row})"

text = f"""🧾 20.08 — что было важного

За сутки 1684 лога. Главное: ночной складской круг снова сложился — 3060 отказов стоковых чанков у двух гигантов утащили склады 77 других магазинов.

1️⃣ Батчи FBS-остатков двух гигантов умерли на дедлайне ретраев: за 20.08 (00:00–09:15 MSK) 3060 `FetchFbsStocksJob` в `failed_jobs` двумя залпами по ~4 минуты — 9420 (1210, 04:50–04:54) и 9477 (1850, 06:08–06:12). Первичного исключения нет, только MaxAttempts: `stockRetryDeadline` короче реального слива общей очереди `products`. Задето аккаунтов: 2, потери есть — у 9420 49 333 строки FBS-остатков не обновлялись с 17.08; у 9477 всё догрузилось в 06:25.
→ Поднять запас в `stockRetryDeadline` или вынести стоковые чанки в отдельную очередь, затем перезапустить failed jobs 9420. {L('c53da709', 4)}

2️⃣ Тот же залп забил `products` и снёс складской синк остальным: 82 отказа `SyncWarehousesJob` у 77 РАЗНЫХ аккаунтов (62 в 04-й час, 20 в 05-й). `retryUntil` у джобы 20 минут, а в 03:40–05:40 очередь занята чанками гигантов — склады 77 магазинов не обновились.
→ Развести стоковые чанки и складской синк по очередям. {L('706c1f45', 10)}

3️⃣ 11 WB-аккаунтов застряли в `expired`, и будить их некому. В 00:22–01:41 WB отдал 34 hard-fail `invalid_grant` по 30 аккаунтам при живом refresh_token (+4 дня по БД); 26 восстановились сами в 09:07–09:18, остальные ушли в `status='expired'` с fail_count=2. Повторов нет: крон `wb:refresh-oauth-tokens` берёт только `->active()`, а `wb:recover-oauth-tokens` в `routes/console.php` не зарегистрирован. Задето аккаунтов: 11, токены умирают 22.08 01:08 — 24.08 00:40, порога 3 не достигают — владельцам не пишут.
→ До 22.08 поставить recover в крон или включить `expired` в выборку refresh. {L('d40f4e00', 12)}

4️⃣ Конкуренция за `orders` третьи сутки ровно та же: 88 deadlock, 70 lock wait, 51 исчерпанный ретрай за 20.08 (00:00–09:15 MSK) против 87/76/48 за то же окно 19.08. `handleOrdersBulk` — 29 строк у 29 аккаунтов, `batchUpdateOrders.uncalculated` — 21, все у 9477; апдейты чанков теряются до следующего синка. Задето: 30 аккаунтов. Он же 9477 четвёртые сутки не досчитывается: 20 отказов `CalculateAccountDayJob`, дни августа неверны.
→ Уменьшить chunkSize в `handleOrdersBulk`, профилировать пересчёт 9477. {L('eba449c8', 9)}

5️⃣ CPO-отчёты Ozon теряются на трёх попытках: 6 финальных отказов за сутки у 6 аккаунтов, каждый день других (сегодня 3879, 8219, 8394, 9640, 9542, демо 3142). На внешний лимит джоба делает `release()` (18 отложек), а лимит стоит на `$tries=3` — три занятых слота и отчёт не загружен; успешно 163.
→ Ограничить ретраи по времени, а не числом. {L('8ccb20c5', 27)}

🔁 Висит без изменений: 9 складов WB без вердикта — 2 дня (fail-open сдвинулся на 21.08); секрет вебхука WB OAuth не задан — 24 дня; ключ reCAPTCHA без миграции — 22 дня; TypeError в `getAccount()` — 24 дня.

💸 Неучтённые операции: за сутки не было.

Со вчера: `FixProductTitlesJob` выдохся — вчера 220 логов и 20 отказов, сегодня 0. Складской батч рецидивировал: падал 17.08, 18–19.08 тихо, сегодня втрое больше. Неразнесённый расход Ozon-рекламы упал вчетверо: 200 017,70 ₽ у 9 аккаунтов против 861 415,59 ₽ у 20.

Фон: ~634 лога в шумных группах. Не оценено: 0.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={GID})"""

print("LENGTH:", len(text))
assert len(text) <= 3900, f"too long: {len(text)}"

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SID)
d = sh.worksheet("Digest")
d.update(values=[["2026-08-20"], [text]], range_name="A1:A2", value_input_option="USER_ENTERED")
print("digest written OK")
