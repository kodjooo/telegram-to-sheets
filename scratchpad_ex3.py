import json, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
orig = json.load(open(BASE + "scratchpad_orig.json"))
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]
print("окно:", cut, "->", last_dt, "| логов:", len(recent))

NEEDLES = [
    ("c4a5b3b0", "Pusher error: cURL error 7"),
    ("01f99585", "Введен не валидный API ключ Wildberries"),
    ("2039b80c", "CheckTransactionDups: account scan failed"),
    ("e619bb34", "Account updating status was cleaned"),
    ("e30a46ae", "app:accounts:clear"),
    ("0b059a9b", "getTransactionsByPeriod failed"),
    ("a2e73dfe", "api_key непригоден"),
    ("1f83387f", "SyncOrders.Batch failed"),
    ("8d3e5816", "SyncOrdersBulkJob: chunk failed"),
    ("0d187abc", "wb-remains-hybrid"),
    ("d7469f93", "ozon-ads-admin-eta"),
    ("5d971d90", "вердикт склада стареет"),
    ("f907d4a7", "расход не разнесён по товарам"),
    ("93e22066", "submit postponed by external rate limit"),
]
for gid, needle in NEEDLES:
    hits = [r for r in recent if needle in r[2]]
    print("\n" + "=" * 90)
    print(f"### #{gid} :: '{needle}' :: за 24ч {len(hits)}")
    hh = collections.Counter(r[1][11:13] for r in hits)
    print("по часам:", dict(sorted(hh.items())))
    for r in hits[:3]:
        print("---", r[0], r[1])
        print(r[2][:1200])
