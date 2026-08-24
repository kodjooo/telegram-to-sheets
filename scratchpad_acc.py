import json, re, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
orig = json.load(open(BASE + "scratchpad_orig.json"))
rows = json.load(open(BASE + "scratchpad_groups.json"))
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]

def accs(sub):
    hits = [r for r in recent if sub in r[2]]
    ids = collections.Counter()
    for r in hits:
        for m in re.finditer(r'"account_?[iI]d":\s*(\d+)', r[2]):
            ids[m.group(1)] += 1
    return len(hits), dict(ids.most_common(12))

for sub in ["WBReportCoverage: marked after repeated empty",
            "Weekly account sync: orders sync error",
            "Weekly account sync: account not found or disabled",
            "[orders] Account updating status was cleaned",
            "Введен не валидный API ключ Wildberries",
            "SyncWarehouses.Batch failed",
            "Unknown transaction type"]:
    n, ids = accs(sub)
    print(f"{sub[:55]:<57} логов={n} аккаунты={ids}")

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

fon = sum(num(r[4]) for r in rows if str(r[9]).strip() == "игнорировать")
tot = sum(num(r[4]) for r in rows)
print("\nВсего за сутки (сумма Groups):", tot, "| фон 'игнорировать':", fon)
print("логов в Original data за 24ч:", len(recent))
print("не обработано:", len([r for r in rows if str(r[8]).strip() != "обработано"]))
