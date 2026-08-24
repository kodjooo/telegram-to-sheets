import json

vals = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups_full.json"))
rows = vals[1:]

def num(x):
    try:
        return int(str(x).replace(" ", "") or 0)
    except Exception:
        return 0

order = sorted(range(len(rows)), key=lambda i: -num(rows[i][3]))
for i in order[:26]:
    r = rows[i] + [""] * (14 - len(rows[i]))
    print("=" * 100)
    print("ROW", i + 2, "| ID", r[0], "| cat", r[1], "| d1", r[3], "d7", r[4], "d30", r[5], "| last", r[6])
    print("PAT:", r[2])
    print("STATUS:", r[7], "| VERDICT:", r[8], "| URG:", r[9], "| CAUSE:", r[10], "| EVAL:", r[12], "| FIRST-ACT:", r[13])
    print("ACTION:", r[11])

print("\n\n### ALL 'действовать' with 'Впервые в действовать' set:")
for i, r0 in enumerate(rows):
    r = r0 + [""] * (14 - len(r0))
    if (r[8] or "").strip() == "действовать":
        print(i + 2, r[0], "d1=", r[3], "d7=", r[4], "first_act=", r[13], "eval=", r[12], "|", r[2][:80])
