import json
from collections import Counter, defaultdict

vals = json.load(open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_orig.json"))
rows = vals[1:]

pats = {
    "FbsStocks": "FetchFbsStocksJob",
    "SyncWarehousesJob": "SyncWarehousesJob has been attempted",
    "FixProductTitles": "FixProductTitlesJob",
    "Deadlock": "Deadlock found",
    "LockWait": "Lock wait timeout",
    "DbConcExh": "DbConcurrencyRetry: exhausted",
    "OAuthHardFail": "WB OAuth refresh hard-fail",
    "OAuthRefreshFailedBackoff": "WB OAuth token refresh failed",
    "OzonAdsRateLimit": "Ozon advertising statistics submit postponed",
    "CalcAccountDay": "CalculateAccountDayJob",
    "OzonCpo": "ozon-cpo] job failed",
    "AdsExport": "ozon-ads-export] расход не разнесён",
    "AdsSpread": "ozon-ads-spread",
    "UnknownTx": "Unknown transaction type",
    "WarehouseRegistry": "WarehouseRegistry",
    "StaleProductId": "stale product_id",
    "ProductDedup": "product-dedup",
    "TransactionOnlineJob": "TransactionOnlineJob",
}

bydate = defaultdict(Counter)
tot = Counter()
last24 = Counter()
CUT = "2026-08-19 06:15"
for r in rows:
    if len(r) < 3:
        continue
    d, txt = r[1], r[2]
    day = d[:10]
    tot[day] += 1
    if d >= CUT:
        last24["__TOTAL__"] += 1
    for k, p in pats.items():
        if p in txt:
            bydate[k][day] += 1
            if d >= CUT:
                last24[k] += 1

print("=== total logs per day (last 6):")
for day in sorted(tot)[-6:]:
    print(" ", day, tot[day])
print("=== rolling 24h since", CUT, "UTC total:", last24["__TOTAL__"])
print()
print(f"{'pattern':28s} {'24h':>6s} {'19.08':>6s} {'20.08':>6s} {'18.08':>6s} {'17.08':>6s}")
for k in pats:
    c = bydate[k]
    print(f"{k:28s} {last24[k]:6d} {c.get('2026-08-19',0):6d} {c.get('2026-08-20',0):6d} {c.get('2026-08-18',0):6d} {c.get('2026-08-17',0):6d}")
