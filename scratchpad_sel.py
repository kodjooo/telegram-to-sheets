import json

rows = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json"))
# cols: 0=rownum,1=ID,2=Категория,3=Шаблон,4=1д,5=7д,6=30д,7=Последнее,8=Статус,9=Вердикт,10=Срочность,11=Причина,12=Действие,13=Оценено,14=Впервые

def num(x):
    try:
        return int(str(x).strip() or 0)
    except Exception:
        return 0

tot1 = sum(num(r[4]) for r in rows)
print("Всего групп:", len(rows), "| сумма за 1 день:", tot1)

unproc = [r for r in rows if str(r[8]).strip() != "обработано"]
print("не обработано:", len(unproc), "| из них с >0 за день:", len([r for r in unproc if num(r[4]) > 0]))

print("\n=== ТИР 1: не обработано, за день > 0 (ERROR сначала) ===")
t1 = [r for r in unproc if num(r[4]) > 0]
t1.sort(key=lambda r: (0 if "ERROR" in str(r[2]).upper() else 1, -num(r[4])))
for r in t1[:40]:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} last={r[7]}")
    print("   ", str(r[3])[:230])

print("\n=== ТИР 2: всплеск x3 (есть вердикт, 1д >= 3*(30д/30)) ===")
t2 = []
for r in rows:
    if str(r[8]).strip() == "обработано" and num(r[4]) >= 3 and num(r[6]) > 0:
        avg = num(r[6]) / 30.0
        if num(r[4]) >= 3 * avg and avg > 0:
            t2.append((num(r[4]) / avg, r))
t2.sort(key=lambda x: -x[0])
for k, r in t2[:20]:
    print(f"row{r[0]} #{r[1]} x{k:.1f} 1d={r[4]} 7d={r[5]} 30d={r[6]} вердикт={r[9]} | {str(r[3])[:150]}")

print("\n=== ТИР 3: системные (стабильно каждый день) ===")
t3 = []
for r in rows:
    d1, d7, d30 = num(r[4]), num(r[5]), num(r[6])
    if d1 >= 2 and d7 >= 5 * d1 and d7 <= 9 * d1 and d30 >= 18 * d1 and d30 <= 34 * d1:
        t3.append(r)
t3.sort(key=lambda r: -num(r[4]))
for r in t3[:25]:
    print(f"row{r[0]} #{r[1]} [{r[2]}] 1d={r[4]} 7d={r[5]} 30d={r[6]} статус={r[8]} вердикт={r[9]} | {str(r[3])[:170]}")

print("\n=== Уже 'действовать' (для блока 'висит без изменений') ===")
for r in rows:
    if str(r[9]).strip() == "действовать":
        print(f"row{r[0]} #{r[1]} 1d={r[4]} 7d={r[5]} 30d={r[6]} срочн={r[10]} впервые={r[14] if len(r)>14 else ''} оценено={r[13]}")
        print("     шаблон:", str(r[3])[:150])
        print("     действие:", str(r[12])[:200])
