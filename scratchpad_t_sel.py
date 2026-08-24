import json
from collections import Counter

vals = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups_full.json"))
hdr = vals[0]
rows = vals[1:]

def num(x):
    try:
        return int(str(x).replace(" ", "").replace(" ", "") or 0)
    except Exception:
        return 0

print("STATUS:", Counter((r[7] if len(r) > 7 else "").strip() for r in rows))
print("VERDICT:", Counter((r[8] if len(r) > 8 else "").strip() for r in rows))
print("CAT:", Counter((r[1] if len(r) > 1 else "").strip() for r in rows))

total_day = sum(num(r[3]) for r in rows)
print("TOTAL D1:", total_day, "TOTAL D7:", sum(num(r[4]) for r in rows), "TOTAL D30:", sum(num(r[5]) for r in rows))

# Tier 1: не обработано & д1>0
t1 = [(i + 2, r) for i, r in enumerate(rows) if (r[7] or "").strip() != "обработано" and num(r[3]) > 0]
print("\nTIER1 not-processed with d1>0:", len(t1))
for n, r in sorted(t1, key=lambda x: -num(x[1][3]))[:40]:
    print(" ", n, r[0], r[1], num(r[3]), num(r[4]), num(r[5]), "|", r[2][:110])

# Tier 2: spikes
t2 = []
for i, r in enumerate(rows):
    d1, d30 = num(r[3]), num(r[5])
    avg = d30 / 30.0
    if d1 >= 3 and avg > 0 and d1 >= 3 * avg:
        t2.append((i + 2, r, d1, avg))
    elif d1 >= 5 and d30 == d1:
        t2.append((i + 2, r, d1, 0.0))
print("\nTIER2 spikes:", len(t2))
for n, r, d1, avg in sorted(t2, key=lambda x: -x[2])[:40]:
    print(" ", n, r[0], r[1], "d1=", d1, "avg=", round(avg, 2), "d7=", num(r[4]), "d30=", num(r[5]), "|", (r[8] or "-"), "|", r[2][:100])

# Tier 4: backlog not processed
t4 = [(i + 2, r) for i, r in enumerate(rows) if (r[7] or "").strip() != "обработано" and num(r[3]) == 0]
print("\nTIER4 backlog:", len(t4))

# top by d1 overall
print("\nTOP20 by d1:")
for i, r in sorted(enumerate(rows), key=lambda x: -num(x[1][3]))[:20]:
    n = i + 2
    print(" ", n, r[0], r[1], num(r[3]), num(r[4]), num(r[5]), "|", (r[8] or "-"), "|", (r[9] or "-"), "|", r[2][:90])
