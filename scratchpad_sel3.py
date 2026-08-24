import json, datetime, collections

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
orig = json.load(open(BASE + "scratchpad_orig.json"))

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

# индексы после добавления номера строки: 0=row,1=ID,2=Категория,3=Шаблон,4=1д,5=7д,6=30д,
# 7=Последнее,8=Статус,9=Вердикт,10=Срочность,11=Причина,12=Действие,13=Оценено,14=Впервые
last_dt = max(r[1] for r in orig[1:] if r[1])
print("Последняя запись Original data:", last_dt)
cut = (datetime.datetime.strptime(last_dt, "%Y-%m-%d %H:%M:%S") - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
recent = [r for r in orig[1:] if r[1] and r[1] >= cut]
print("Окно 24ч с:", cut, "| логов:", len(recent))

print("\n=== СТАТУСЫ ===")
print(collections.Counter(r[8] for r in rows))
print(collections.Counter(r[9] for r in rows))

print("\n=== ТИР1: не обработано и 1д>0 ===")
t1 = [r for r in rows if r[8].strip() != "обработано" and num(r[4]) > 0]
t1.sort(key=lambda r: (0 if "ERROR" in r[2].upper() else 1, -num(r[4])))
for r in t1:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} посл={r[7]}")
    print("    ", str(r[3])[:200])

print("\n=== ТИР2: всплеск x3 (1д >= 3*30д/30), уже обработано ===")
t2 = []
for r in rows:
    d1, d30 = num(r[4]), num(r[6])
    if r[8].strip() == "обработано" and d1 >= 3 and d30 > 0 and d1 >= 3 * (d30 / 30.0):
        t2.append((d1 / max(d30 / 30.0, 0.01), r))
t2.sort(key=lambda x: -x[1][4] if isinstance(x[1][4], int) else -num(x[1][4]))
for k, r in sorted(t2, key=lambda x: -num(x[1][4])):
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} x{k:.1f} | в={r[9]} оц={r[13]}")
    print("    ", str(r[3])[:170])

print("\n=== ВСЕ с 1д>0 (обзор) ===")
act = [r for r in rows if num(r[4]) > 0]
act.sort(key=lambda r: -num(r[4]))
tot = sum(num(r[4]) for r in act)
print("групп:", len(act), "сумма 1д:", tot)
for r in act:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} | ст={r[8]} в={r[9]} ср={r[10]} оц={r[13]} впервые={r[14] if len(r)>14 else ''}")
    print("    ", str(r[3])[:170])

print("\n=== 'действовать' без событий за сутки (кандидаты в 'висит') ===")
for r in rows:
    if r[9].strip() == "действовать" and num(r[4]) == 0:
        print(f"row{r[0]} #{r[1]} 1d={r[4]} 7d={r[5]} 30d={r[6]} впервые={r[14] if len(r)>14 else ''} | {str(r[3])[:110]}")
