import json, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
orig = json.load(open(BASE + "scratchpad_orig.json"))

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

print("=== ВСЕ с 1д>0, хвост (после row37) ===")
act = [r for r in rows if num(r[4]) > 0]
act.sort(key=lambda r: -num(r[4]))
for r in act[36:]:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} | ст={r[8]} в={r[9]} ср={r[10]} оц={r[13]} впервые={r[14] if len(r)>14 else ''}")
    print("    ", str(r[3])[:170])

print("\n=== 'действовать' 1д=0, впервые >= 2026-07-28 (кандидаты в 'висит') ===")
for r in rows:
    if r[9].strip() == "действовать" and num(r[4]) == 0:
        fv = r[14] if len(r) > 14 else ""
        if fv and fv >= "2026-07-28":
            print(f"row{r[0]} #{r[1]} 1d={r[4]} 7d={r[5]} 30d={r[6]} впервые={fv} оц={r[13]}")
            print("    ", str(r[3])[:150])
            print("    ДЕЙСТВИЕ:", str(r[12])[:250])

print("\n=== DATA: Unknown transaction type за сутки (по Original data) ===")
last_dt = max(r[1] for r in orig[1:] if r[1])
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
cnt = 0
for r in orig[1:]:
    if r[1] and r[1] >= cut and "Unknown transaction type" in r[2]:
        cnt += 1
        print(r[1], "|", r[2][:600])
print("ИТОГО unknown tx за сутки:", cnt)
