import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
SID = cfg["google_sheet_id"]
GID_GROUPS = 1412410715
GID_UNKNOWN = 1830882857

def link(gid, row):
    return f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid={GID_GROUPS}&range=A{row}"

L = lambda gid, row: f"[#{gid} · строка {row}]({link(gid, row)})"

text = f"""🧾 20.08 — что было важного

За сутки 1684 лога. Главное: ночной складской круг снова сложился — 3060 отказов стоковых чанков у двух гигантов утащили за собой складской синк 77 других магазинов.

1️⃣ Батчи FBS-остатков двух крупных аккаунтов умерли на дедлайне ретраев: за 20.08 (00:00–09:15 MSK) 3060 `FetchFbsStocksJob` в `failed_jobs`, двумя залпами по ~4 минуты — 9420 (1210 отказов, 04:50–04:54) и 9477 (1850, 06:08–06:12). Первичного исключения нет, только MaxAttempts: `stockRetryDeadline` (хвост стаггера + теоретический слив + час запаса) короче реального времени слива общей очереди `products`. Задето аккаунтов: 2, и потери есть — у 9420 49 333 строки FBS-остатков не обновлялись с 17.08, третьи сутки; у 9477 всё догрузилось (199 103 строки обновлены в 06:25).
→ Поднять запас в `stockRetryDeadline` либо вынести стоковые чанки в отдельную очередь, затем перезапустить failed jobs аккаунта 9420. {L('c53da709', 4)}

2️⃣ Тот же залп забил очередь `products` и снёс складской синк остальным: 82 отказа `SyncWarehousesJob` у 77 РАЗНЫХ аккаунтов (62 в 04-й час, 20 в 05-й). У джобы `retryUntil` всего 20 минут от постановки, а в 03:40–05:40 очередь занята чанками гигантов — склады 77 магазинов за сутки не обновились.
→ Развести стоковые чанки и обычный складской синк по разным очередям или поднять `retryUntil`. {L('706c1f45', 10)}

3️⃣ 11 WB-аккаунтов застряли в `expired`, и будить их некому. Ночью в 00:22–01:41 WB отдал 34 hard-fail `invalid_grant` («refresh token is expired») по 30 аккаунтам — при живом refresh_token (+4 дня по БД); 26 из 30 восстановились сами в 09:07–09:18. Остальные ушли в `status='expired'` с fail_count=2, и повторов нет: крон `wb:refresh-oauth-tokens` (*/5) берёт только `->active()`, а `wb:recover-oauth-tokens` в `routes/console.php` не зарегистрирован вообще. Задето аккаунтов: 11, их refresh-токены умирают 22.08 01:08 — 24.08 00:40, порога 3 они не достигают, поэтому владельцам ничего не уходит (`user_notified_at`=NULL).
→ До 22.08 поставить recover в расписание или включить `expired` в выборку refresh — иначе останется только переподключение магазина руками. {L('d40f4e00', 12)}

4️⃣ Конкуренция за `orders` третьи сутки на том же уровне: за 20.08 (00:00–09:15 MSK) 88 deadlock, 70 lock wait и 51 исчерпанный ретрай против 87/76/48 за то же окно 19.08. По op: `handleOrdersBulk` — 29 строк у 29 аккаунтов, `batchUpdateOrders.uncalculated` — 21, все у 9477. Задето аккаунтов: 30, апдейты этих чанков теряются до следующего синка.
→ Уменьшить chunkSize в `handleOrdersBulk` и развести по времени ночной скан дублей. {L('eba449c8', 9)}

5️⃣ Пересчёт аккаунта 9477 не сходится четвёртые сутки: 20 отказов `CalculateAccountDayJob` за 20.08 (12 таймаутов, 5 MaxAttempts, 3 lock wait), все у него одного; он же даёт 21 из 51 исчерпанного ретрая выше. Задето аккаунтов: 1, но его дни августа остаются недосчитанными.
→ Профилировать пересчёт 9477, разбить день на части или поднять timeout джобы. {L('4071d27f', 13)}

6️⃣ CPO-отчёты Ozon теряются на трёх попытках: 6 финальных отказов `SyncAdvertisingOzonCpoReportsJob` за 20.08 у 6 аккаунтов, каждый день других (сегодня 3879, 8219, 8394, 9640, 9542 и демо 3142). Причина в коде: на внешний лимит джоба делает `release()` (18 отложек за сутки), а ограничение стоит на `$tries=3` — три занятых слота и отчёт не загружен; успешно за сутки 163.
→ Ограничить ретраи по времени (`retryUntil`), а не числом попыток. {L('8ccb20c5', 27)}

🔁 Висит без изменений: 9 складов WB без свежего вердикта — 2 дня, статус не менялся (fail-open сдвинулся на тик 21.08); секрет вебхука WB OAuth disconnect не задан — 24 дня; ключ reCAPTCHA требует миграции — 22 дня; TypeError в `AccountService::getAccount()` — 24 дня.

💸 Неучтённые операции: за сутки не было.

Со вчера: `FixProductTitlesJob` выдохся — вчера 220 логов и 20 финальных отказов, сегодня 0 (в прод-логе только 49 INFO-строк постановки, в `failed_jobs` 0). Рецидивировал складской батч: падал 17.08, 18–19.08 было тихо, сегодня втрое больше. Неразнесённый расход Ozon-рекламы упал вчетверо — 200 017,70 ₽ у 9 аккаунтов против 861 415,59 ₽ у 20 за то же окно вчера.

Фон: ~634 лога в известных шумных группах. Не оценено: 0.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={GID_GROUPS})"""

print("LENGTH:", len(text))
assert len(text) <= 3900, "too long"

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SID)
d = sh.worksheet("Digest")
d.update(values=[["2026-08-20"], [text]], range_name="A1:A2", value_input_option="USER_ENTERED")
print("digest written")
print(text[:400])
