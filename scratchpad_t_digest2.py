# -*- coding: utf-8 -*-
import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app"
TODAY = "2026-08-23"
cfg = json.load(open(BASE + "/config.json"))
SID = cfg["google_sheet_id"]
GID_GROUPS = 1412410715
GID_UNK = 1830882857
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "/google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SID)
g = sh.worksheet("Groups")
vals = g.get_all_values()
rowof = {}
for i, r in enumerate(vals[1:], start=2):
    if r and r[0]:
        rowof[r[0].strip()] = i

def L(gid):
    r = rowof.get(gid, 0)
    return "[#%s · строка %d](https://docs.google.com/spreadsheets/d/%s/edit#gid=%d&range=A%d)" % (gid, r, SID, GID_GROUPS, r)

TABLE = "https://docs.google.com/spreadsheets/d/%s/edit#gid=%d" % (SID, GID_GROUPS)
UNK = "https://docs.google.com/spreadsheets/d/%s/edit#gid=%d" % (SID, GID_UNK)

txt = """🧾 23.08 — что было важного

За сутки 993 лога. Главное: пересчёт аккаунта 9477 не сходится третьи сутки и своими блокировками задевает ещё 20 магазинов.

1️⃣ За 23.08 (00:00–11:06 MSK) 15 отказов `CalculateAccountDayJob`, все — аккаунт 9477, даты 16–18.08; в `failed_jobs` за то же окно 15, за 7 дней 262. Динамики нет: 21.08 — 44, 22.08 — 40, сегодня 15. В БД у 9477 1 170 924 заказа из 2 551 655 с `is_calculated=0`. Его update по `orders` (до 33 520 строк в транзакции) роняют соседей: 50 исчерпаний `DbConcurrencyRetry` у 21 аккаунта и 47 deadlock-строк за сутки, у 20 соседей ретраи добивают. Задето аккаунтов: 1 с устаревшими данными + 20 с задержками.
→ Увести пересчёт 9477 в отдельную очередь и порезать batch update на чанки. {a1}

2️⃣ Обвал остатков 21.08 не случился: порог подняли руками, в прод-`.env` стоит `WAREHOUSE_ACTIVITY_STALE_AFTER_DAYS=10` вместо дефолтных 3, и лог 23.08 01:00 даёт `stale_after_days:10` при `last_verdict_date=2026-08-16` — fail-open сдвинулся на 26.08. Отсрочка, не починка: 5 складов WB FBO без свежего вердикта, тик 05:10 добавил в inactive ещё 4 с `ratio=0.0` — под вычетом 375 461 единица остатка. Задето складов: 9 из 192; радиус по аккаунтам не измерен — в логе реестра нет `account_id`.
→ До 26.08 восстановить источник движений по этим складам либо принять возврат остатков. {a2}

3️⃣ Сигнал 23.08 08:00 MSK: у 29 активных WB-магазинов нет stable-отчёта 10 дней, с 13.08; у 18 из них нет права «Финансы» — данные сами не появятся. За сутки ещё 10 пустых недельных отчётов при активности, 5 недель помечены непокрытыми (9555, 8659, 8275, 9955, 9966). Задето аккаунтов: 29, у 5 конкретные недели без финансовых данных.
→ По 18 аккаунтам запросить у владельцев право «Финансы», по 11 остальным поставить дозагрузку недель. {a3}

4️⃣ Ozon отдаёт «Превышен лимит активных запросов (максимум 1)»: за сутки 239 отложек у 5 кабинетов, четверо сделали по 55–56 попыток каждые 30 с. Джоба крутит `release()` до дедлайна `retryUntil()` и умирает по MaxAttempts — в 05:10 упали три (8397, 5681, 8271). Повтор в 05:52–06:55 закрыл только 23.07–23.08, ретро 01.05–23.07 не загружено. Задето аккаунтов: 3, неполный расход «Оплата за заказ» за три месяца.
→ Сериализовать CPO-запросы (один активный отчёт на кабинет) и продлить `retryUntil`. {a4}

5️⃣ 23.08 11:05 MSK WB отклонил окно `content` у 28 аккаунтов (21.08 было 23 при пороге 15) — рецидив, значит не разовая аномалия. Отдельно у платящего магазина 8480 синк заказов не идёт с 21.08 10:13 (7 неудач подряд), а Ozon держит запрет `accrualByDay` для 8487: 6 отказов и 1 финальный `TransactionsJob` за сутки. Задето аккаунтов: 28 + 2.
→ Уточнить у WB доступ к content-окну; связаться с владельцем 8480; по 8487 эскалировать метод в Ozon, ключ не перевыпускать. {a5}

🔁 Висит без изменений: пустые себестоимости отклоняются без понятного сообщения — 11 дней. Финальные падения `FixProductTitlesJob` — 17 дней.

💸 Неучтённые операции: за сутки не было. [вкладка Unknown tx]({unk})

Со вчера: всплеск зависаний синка транзакций сошёл — 4 лога вместо 222 и 3 очистки статуса вместо 183. «Массовое ограничение Ozon» не подтвердилось: по `TransactionsJob` за сутки 1 финальный отказ. Рецидивировали 9477, лимиты Ozon CPO и отказы WB по content.

Фон: ~660 логов в известных шумных группах. Не оценено: 0.

👉 [Вся таблица]({tbl})"""

txt = txt.format(
    a1=L("4071d27f"), a2=L("5d971d90"), a3=L("51af90b5"),
    a4=L("8ccb20c5"), a5=L("0da31129"),
    unk=UNK, tbl=TABLE)

print("LEN:", len(txt))
if len(txt) > 3900:
    raise SystemExit("TOO LONG, not written")

d = sh.worksheet("Digest")
d.update(values=[[TODAY]], range_name="A1", value_input_option="USER_ENTERED")
d.update(values=[[txt]], range_name="A2", value_input_option="USER_ENTERED")
print("DIGEST WRITTEN OK")
