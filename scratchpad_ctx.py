import json, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
orig = json.load(open(BASE + "scratchpad_orig.json"))
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]

TARGETS = [
    ("38212eaf", "WBReportCoverage: marked after repeated empty"),
    ("afb707d7", "Weekly account sync: orders sync error"),
    ("2972259c", "Weekly account sync: account not found or disabled"),
    ("a14bf0d0", "Allowed memory size of"),
    ("6c05c661", "SyncWarehouses.Batch failed"),
    ("bcae9d7b", "[OzonPerformanceAuthBackoff] hard-fail"),
    ("e2f9a066", "auth hard-fail"),
    ("e402dbf5", "Payment failed synchronously at adapter"),
    ("ec3bda8d", "Tinkoff recurring charge declined"),
    ("dfcb157c", "[orders] Account updating status was cleaned"),
    ("8f8b5d8a", "Введен не валидный API ключ Wildberries"),
]
for gid, sub in TARGETS:
    hits = [r for r in recent if sub in r[2]]
    print("=" * 100)
    print("#%s  '%s'  за 24ч: %d" % (gid, sub, len(hits)))
    times = collections.Counter(r[1][:13] for r in hits)
    print("по часам:", dict(sorted(times.items())))
    for r in hits[:3]:
        print("  ", r[1], r[2][:700].replace("\n", " "))
