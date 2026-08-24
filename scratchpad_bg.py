import json
BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
def num(x):
    try: return int(str(x).strip() or 0)
    except Exception: return 0
IGN = {"f907d4a7","16ad5659","8ccb20c5","4e83d7bf","fd92d782","eb3d6426","3bb157c8","3f40ef58"}
bg = sum(num(r[4]) for r in rows if r[9].strip() == "игнорировать")
print("фон (игнорировать) за сутки:", bg)
print("не оценено после прогона:", sum(1 for r in rows if r[8].strip() != "обработано" and r[1] not in IGN))
print("всего за сутки:", sum(num(r[4]) for r in rows))
for r in rows:
    if r[1] in ("a8523504","2205ee4b","d0aa68ff","38212eaf","3fb6e6ea","0c3bc690"):
        print(f"#{r[1]} 1d={r[4]} 7d={r[5]} впервые={r[14] if len(r)>14 else ''} | {str(r[3])[:90]}")
