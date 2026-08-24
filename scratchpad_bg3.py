import json
rows = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json"))

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

ign = 0
obs = 0
act = 0
unproc = 0
tot = 0
for r in rows:
    r = r + [""] * (15 - len(r))
    d1 = num(r[4])
    tot += d1
    v = (r[9] or "").strip()
    st = (r[8] or "").strip()
    if st != "обработано":
        unproc += d1
    elif v == "игнорировать":
        ign += d1
    elif v == "понаблюдать":
        obs += d1
    elif v == "действовать":
        act += d1
print("total d1:", tot)
print("игнорировать:", ign)
print("понаблюдать:", obs)
print("действовать:", act)
print("не обработано (logs):", unproc)
print("не обработано (groups):", sum(1 for r in rows if (r + [""] * 15)[8].strip() != "обработано"))
