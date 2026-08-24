# -*- coding: utf-8 -*-
import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
TODAY = "2026-08-19"
SID = "1eSuLIAlnxkZHA4jy2cBZVwWiA__NZ3pl5hncxU7O3RU"
G = 1412410715


def link(gid, row):
    return f"[#{gid} · строка {row}](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={G}&range=A{row})"


TEXT = f"""🧾 19.08 — что было важного

За сутки 3051 лог. Главное: 9 складов WB третьи сутки без вердикта — завтра предохранитель скачком вернёт остатки 118 магазинам.

1️⃣ Реестр складов не выносит вердикты третьи сутки: тик 19.08 05:10 MSK — `evaluated=187`, `skipped_no_verdict=185`, `flagged_without_verdict=9`. В БД у всех 9 складов `inactive=1` и `last_verdict_date=2026-08-16` при `last_evaluated_date=2026-08-18`. Задето аккаунтов: 118 — столько магазинов держат остатки на этих складах в снимке за 18.08, всего 188 548 единиц, и сейчас они вычитаются из расчётов. Сегодня `diffInDays=3` при `stale_after_days=3`, то есть вычет ещё применяется, а 20.08 fail-open его снимет.
→ Восстановить источник движений по складам до 20.08 либо осознанно принять возврат остатков. {link("5d971d90", 2)}

2️⃣ Заказы: за 19.08 до 09:15 MSK 48 исчерпанных ретраев `DbConcurrencyRetry` (за 18.08 целиком 162), рядом 87 deadlock и 76 lock wait. Разложено по `op`: `handleOrdersBulk` — 27 строк у 25 разных аккаунтов, `batchUpdateOrders.uncalculated` — 21 строка, вся у одного аккаунта 9477. По часам ровно 2–11 в час круглосуточно — это постоянная конкуренция за `orders`, а не залп; апдейты этих чанков теряются до следующего синка.
→ Уменьшить `chunkSize` в `handleOrdersBulk` и развести по времени ночной скан дублей. {link("eba449c8", 7)}

3️⃣ Реклама Ozon: за 19.08 до 09:15 MSK 20 логов, задето аккаунтов: 20, не разнесено 861 415,59 ₽ расхода (14 аккаунтов — `no_weights_no_composition`, 6 — `non_sku_campaign`). Накануне за полные сутки было 32 аккаунта и 1 561 063 ₽ — темп тот же. Деньги остаются в сводной «Рекламе» кабинета и не доходят до юнит-экономики товара.
→ Решить, как разносить кампании без весов и состава SKU. {link("f907d4a7", 17)}

4️⃣ Пересчёт: за сутки 20 отказов `CalculateAccountDayJob`, все у аккаунта 9477 и только по трём датам — 15.08 (9 раз), 14.08 (6), 13.08 (6). У него же 285 успешных пересчётов за те же сутки и 21 исчерпанный ретрай из пункта 2: аккаунт тяжёлый, и эти три дня не досчитываются раз за разом. Задето аккаунтов: 1.
→ Профилировать пересчёт 9477 за 13–15.08: поднять timeout или разбить день на части. {link("4071d27f", 18)}

5️⃣ CPO-отчёты Ozon: вторые сутки подряд после выкатки джобы — 6 финальных отказов `SyncAdvertisingOzonCpoReportsJob` (5 в 04:42 MSK, 1 в 06:53), в `failed_jobs` с 00:00 MSK те же 6. Задето аккаунтов: 6, CPO за окно 01.05–19.08 у них не загружен. Трёх попыток на занятый слот отчётов Ozon не хватает.
→ Поднять `tries`/`backoff` под слот отчётов Ozon. {link("8ccb20c5", 36)}

🔁 Висит без изменений: closure после деплоя — 21 день; Ozon `getTransactionsByPeriod` — 18 дней, 0 событий за сутки; Forte «ТЕСТ»-платёж — 22 дня.

💸 Неучтённые операции: за сутки не было.

Со вчера: названия товаров WB почти вылечились — 20 финальных `FixProductTitlesJob` у одного аккаунта 4584 против 157 у 50 накануне; `TransactionOnlineJob` 0 против 68; WB OAuth refresh 1 против 230; weekly-отчёт 10–16.08 разослан 18.08 в 09:37 MSK. Новое: soketi лежал 18.08 17:16:16–17:17:07 MSK — 117 realtime-уведомлений интерфейса не доставлено, данные не затронуты.

Фон: ~823 лога в известных шумных группах. Не оценено: 0.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={G})"""

print("ДЛИНА:", len(TEXT))
assert len(TEXT) <= 3900, "слишком длинно"

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
cfg = json.load(open(BASE + "config.json"))
sh = gc.open_by_key(cfg["google_sheet_id"])
d = sh.worksheet("Digest")
d.update(values=[[TODAY], [TEXT]], range_name="A1:A2", value_input_option="USER_ENTERED")
print("Дайджест записан. A1 =", TODAY)
