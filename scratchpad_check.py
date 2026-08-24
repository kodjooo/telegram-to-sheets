import json

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
IDS = ["b405e930", "2eb3a756", "a14bf0d0", "38212eaf", "afb707d7", "2972259c",
       "6c05c661", "bcae9d7b", "e2f9a066", "e402dbf5", "ec3bda8d", "dfcb157c", "8f8b5d8a"]
by = {r[1]: r for r in rows}
for i in IDS:
    r = by.get(i)
    if not r:
        print("НЕ НАЙДЕНА", i)
        continue
    print("=" * 90)
    print(f"row{r[0]} #{r[1]} 1d={r[4]} 7d={r[5]} 30d={r[6]} | статус={r[8]} | вердикт={r[9]} | срочн={r[10]} | прич={r[11]} | оц={r[13]} | впервые={r[14] if len(r) > 14 else ''}")
    print("ШАБЛОН:", str(r[3])[:160])
    print("ДЕЙСТВИЕ:", r[12])
