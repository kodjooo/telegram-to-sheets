import json, re, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
orig = json.load(open(BASE + "scratchpad_orig.json"))

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

# ---- окно: последние 24 часа по колонке Дата вкладки Original data
last_dt = max(r[1] for r in orig[1:] if r[1])
print("Последняя запись Original data:", last_dt)
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
print("Окно 24ч с:", cut)
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]
print("Логов за 24ч:", len(recent))

# ---- Неучтённые операции за сутки
print("\n=== DATA: Unknown transaction type за 24ч ===")
unk = [r for r in recent if "Unknown transaction type" in r[2]]
print("всего логов:", len(unk))
cnt = collections.Counter()
for r in unk:
    t = r[2]
    parts = []
    for key in ("operation_type", "supplier_oper_name", "bonus_type_name", "operation_type_name", "doc_type_name"):
        m = re.search(r'"%s":\s*("((?:[^"\\]|\\.)*)"|null)' % key, t)
        v = m.group(2) if (m and m.group(2) is not None) else None
        if v:
            parts.append(key + "=" + v)
    cnt[" | ".join(parts) if parts else "НЕТ ПОЛЕЙ: " + t[:200]] += 1
for k, v in cnt.most_common(30):
    print(v, "шт ::", k[:260])
if unk:
    print("--- примеры (сырые) ---")
    for r in unk[:3]:
        print(r[0], r[1], r[2][:600])
        print()

# ---- необработанная группа
print("\n=== Примеры для #b405e930 (ozon-file-map-auto) ===")
for r in recent:
    if "ozon-file-map-auto" in r[2]:
        print(r[0], r[1], r[2][:500])
        print()

# ---- тир2/тир3 с датой оценки
print("\n=== Группы за 24ч с событиями: статус/вердикт/оценено ===")
act = [r for r in rows if num(r[4]) > 0]
act.sort(key=lambda r: -num(r[4]))
for r in act:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} | в={r[9]} | оц={r[13]} | впервые={r[14] if len(r)>14 else ''}")
    print("    ", str(r[3])[:130])
