# -*- coding: utf-8 -*-
import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
SID = cfg["google_sheet_id"]
G = 1412410715
U = 1830882857
TODAY = "2026-08-18"

def L(gid, row):
    return f"[#{gid} · строка {row}](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={G}&range=A{row})"

text = f"""🧾 18.08 — что было важного

За сутки 4418 логов. Главное: у 9 складов WB второй день не обновляется вердикт активности — 19–20.08 предохранитель сам снимет логистический вычет.

1️⃣ Реестр складов перестал выносить вердикты: тик 18.08 05:10 MSK — `skipped_no_verdict=185` из 187, `flagged_without_verdict=9`. В БД у всех 9 помеченных складов WB `last_verdict_date=2026-08-16` при `last_evaluated_date=2026-08-17` и `coverage_accounts=0`. За сутки 1748 предупреждений против ~835/сутки за 7 дней. При `stale_after_days=3` fail-open снимет вычет 19–20.08, остатки вернутся в расчёт. Радиус не измерен: привязки склад→аккаунт в реестре нет.
→ Восстановить источник движений по складам до 19.08. {L('5d971d90', 2)}

2️⃣ Деплой рекламы Ozon 17.08 16:02 MSK включил диагностику разноса: за сутки 44 лога, задето аккаунтов: 32, 1 561 063 ₽ расхода не попали ни в одну карточку (1 317 819 ₽ — `no_weights_no_composition`, 243 244 ₽ — `non_sku_campaign`). Деньги висят в сводной «Рекламе».
→ Решить, как разносить кампании без весов и состава SKU. {L('f907d4a7', 15)}

3️⃣ Заказы: за сутки 43 `DbConcurrencyRetry: exhausted`, все `op=handleOrdersBulk` при `chunkSize=99`; задето аккаунтов: 17. По часам 00–07 ровно 2–9 в час — постоянная конкуренция за `orders` (рядом 50 deadlock и 104 lock wait). Апдейты этих чанков теряются до следующего синка.
→ Уменьшить `chunkSize` в `handleOrdersBulk`. {L('eba449c8', 14)}

4️⃣ Названия товаров WB: в `failed_jobs` с 00:00 MSK 157 финальных `FixProductTitlesJob` (часы 05–08), задето аккаунтов: 50. Второй день подряд (17.08 — 177), ретраи не добили.
→ Поднять пропускную способность очереди WB-продуктов и перезапустить сегодняшние failed jobs. {L('09170105', 8)}

5️⃣ Weekly-отчёт за 10–16.08 не закрыт: 254 предупреждения за сутки против ~33/сутки за 30 дней, `stalled_ticks=88`, `wb_working=0` при `ozon_working=552` — WB-часть недели не пришла вовсе, гейт ждёт её без предела.
→ Дозагрузить WB-часть недели и дать гейту таймаут. {L('247083c8', 5)}

🔁 Висит без изменений: closure после деплоя — 20 дней; Ozon `getTransactionsByPeriod` — 17 дней, 0 событий за сутки; Forte «ТЕСТ»-платёж — 21 день.

💸 Неучтённые операции за сутки: Подписка Premium Plus — 1 шт, Обработка отправления Drop-off (СЦ) — 1 шт, Обработка отправления Drop-off (ПВЗ) — 1 шт, Обработка отправления Drop-off партнёрами (ПВЗ) — 1 шт, Грузоместо: Сортировка по составу — 1 шт, Грузоместо: Корректировка состава — 1 шт, Отгрузка в нерекомендованный слот — 1 шт, Отгрузка в нерекомендованный слот - отмена начисления — 1 шт, Превышение индекса ошибок: просроченная отгрузка — 1 шт, Дополнительная обработка ОВХ — 1 шт, Декомпенсации и возвращение товаров на склад — 1 шт, Благотворительное пожертвование — 1 шт. Разбивка: [вкладка Unknown tx](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={U})

Со вчера: складской залп выдохся — с 00:00 MSK `FetchFbsStocksJob` 0 в `failed_jobs` против 1256; OOM не повторился (0 против 3). Новое: 10 финальных `TransactionOnlineJob` у 10 аккаунтов и 7 отказов нового `SyncAdvertisingOzonCpoReportsJob` — без CPO остался 1 аккаунт.

Фон: ~1188 логов в известных шумных группах. Не оценено: 0.

👉 [Вся таблица](https://docs.google.com/spreadsheets/d/{SID}/edit#gid={G})"""

print("ДЛИНА:", len(text))

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SID)
d = sh.worksheet("Digest")
d.update(values=[[TODAY]], range_name="A1", raw=False)
d.update(values=[[text]], range_name="A2", raw=False)
print("=== ЗАПИСАНО ===")
print("A1:", d.acell("A1").value)
print("A2 длина:", len(d.acell("A2").value or ""))
