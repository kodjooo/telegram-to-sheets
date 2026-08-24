import json

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
rows = json.load(open(BASE + "scratchpad_groups.json"))
IDS = ["f907d4a7","16ad5659","8ccb20c5","4e83d7bf","fd92d782","eb3d6426","3bb157c8","3f40ef58",
       "5d971d90","247083c8","6c1fd8f8","bcacc883","09170105","0ee710cd","9ad49eef","ffadd2fb",
       "eba449c8","2b513aa8","33487387","a14bf0d0","bcae9d7b","c6ff1e3e","e2f9a066"]
idx = {r[1]: r for r in rows}
for i in IDS:
    r = idx.get(i)
    if not r:
        print("!!! НЕ НАЙДЕН", i); continue
    print(f"--- #{i} row{r[0]} | H={r[8]} I={r[9]} J={r[10]} K={r[11]} M={r[13]} N={r[14] if len(r)>14 else ''}")
    print("   L:", r[12])
