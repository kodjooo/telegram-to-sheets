import json

rows = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json"))
# row = [rownum, ID, Cat, Pattern, d1, d7, d30, last, status, verdict, urg, cause, action, evaluated, first_act]

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

tot_d1 = 0
unproc = []
spikes = []
processed = []
for r in rows:
    r = r + [""] * (15 - len(r))
    rn, gid, cat, pat, d1, d7, d30, last, status, verdict, urg, cause, action, ev, fa = r[:15]
    d1n, d7n, d30n = num(d1), num(d7), num(d30)
    tot_d1 += d1n
    st = (status or "").strip()
    if st != "обработано":
        unproc.append((d1n, d7n, d30n, rn, gid, cat, pat, st))
    else:
        processed.append((d1n, d7n, d30n, rn, gid, cat, pat, verdict, urg, action, ev, fa))
        avg = d30n / 30.0
        if d1n >= 3 * avg and d1n >= 5:
            spikes.append((d1n, d7n, d30n, rn, gid, cat, pat, verdict, action, ev))

print("TOTAL d1 =", tot_d1, "| groups:", len(rows), "| unprocessed:", len(unproc))
print()
print("=== UNPROCESSED with d1>0 (sorted ERROR first, then d1 desc) ===")
u = [x for x in unproc if x[0] > 0]
err = sorted([x for x in u if "ERROR" in (x[5] or "").upper() or "ERROR" in (x[6] or "")[:40].upper()], key=lambda x: -x[0])
oth = sorted([x for x in u if x not in err], key=lambda x: -x[0])
for x in err + oth:
    print(f"d1={x[0]:5d} d7={x[1]:6d} d30={x[2]:6d} row={x[3]:4d} ID={x[4]} cat={x[5]}")
    print("    ", (x[6] or "")[:300].replace("\n", " "))
print()
print("=== UNPROCESSED with d1==0 : count =", len([x for x in unproc if x[0] == 0]))
print()
print("=== SPIKES (processed, d1 >= 3x avg30 and d1>=5) ===")
for x in sorted(spikes, key=lambda x: -x[0]):
    print(f"d1={x[0]:5d} d7={x[1]:6d} d30={x[2]:6d} avg={x[2]/30:.1f} row={x[3]:4d} ID={x[4]} verdict={x[7]} ev={x[9]}")
    print("    ", (x[6] or "")[:200].replace("\n", " "))
print()
print("=== TOP processed by d1 (context / background) ===")
for x in sorted(processed, key=lambda x: -x[0])[:25]:
    print(f"d1={x[0]:5d} d7={x[1]:6d} d30={x[2]:6d} row={x[3]:4d} ID={x[4]} v={x[7]} u={x[8]} ev={x[10]} firstact={x[11]}")
    print("    ", (x[6] or "")[:160].replace("\n", " "))
print()
print("=== ALL 'действовать' groups ===")
for x in sorted([p for p in processed if (p[7] or "").strip() == "действовать"], key=lambda x: -x[0]):
    print(f"d1={x[0]:5d} d7={x[1]:6d} d30={x[2]:6d} row={x[3]:4d} ID={x[4]} urg={x[8]} ev={x[10]} firstact={x[11]}")
    print("    pat:", (x[6] or "")[:160].replace("\n", " "))
    print("    act:", (x[9] or "")[:300].replace("\n", " "))
