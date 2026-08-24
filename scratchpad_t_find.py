import json

vals = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups_full.json"))
rows = vals[1:]

needles = ["WbSpecializedDenials", "WbReportGap", "OrdersSyncStuck", "cost-price", "ozon-cpo",
           "SyncProductsPricesFromOrders", "источник пуст при неп", "WarehouseRegistry"]
for nd in needles:
    print("\n=== " + nd)
    for i, r in enumerate(rows):
        if nd.lower() in (r[2] or "").lower():
            rr = r + [""] * (14 - len(r))
            print("  row", i + 2, rr[0], "d1=", rr[3], "d7=", rr[4], "d30=", rr[5], "| V:", rr[8], "| eval:", rr[12], "| first:", rr[13])
            print("     PAT:", rr[2][:150])
            print("     ACT:", (rr[11] or "")[:200])
