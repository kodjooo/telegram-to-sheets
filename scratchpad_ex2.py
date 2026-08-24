import json, re, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
orig = json.load(open(BASE + "scratchpad_orig.json"))
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]
print("окно:", cut, "->", last_dt, "| логов:", len(recent))

NEEDLES = [
    ("f907d4a7", "ozon-ads-export] расход не разнесён по товарам"),
    ("16ad5659", "в окне нет авторитетного расхода кампаний"),
    ("8ccb20c5", "[advertising][ozon-cpo] job failed"),
    ("4e83d7bf", "SyncAdvertisingOzonCpoReportsJob has been attempted"),
    ("fd92d782", "popup-кэш расхода не обновлён"),
    ("eb3d6426", "источник пуст при непустых расходах"),
    ("3bb157c8", "IAOJ: bulk-invalidation lock timeout"),
    ("3f40ef58", "[FIX] FixProductTitlesJob: failed"),
    ("5d971d90", "вердикт склада стареет"),
    ("9ad49eef", "TransactionOnlineJob has been attempted too many times"),
]
for gid, needle in NEEDLES:
    hits = [r for r in recent if needle in r[2]]
    print("\n" + "=" * 90)
    print(f"### #{gid} :: '{needle}' :: за 24ч {len(hits)}")
    hh = collections.Counter(r[1][11:13] for r in hits)
    print("по часам:", dict(sorted(hh.items())))
    for r in hits[:3]:
        print("---", r[0], r[1])
        print(r[2][:1400])
