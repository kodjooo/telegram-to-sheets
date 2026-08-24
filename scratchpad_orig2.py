import json, gspread
from collections import Counter, defaultdict
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

ws = sh.worksheet("Original data")
vals = ws.get_all_values()
print("rows:", len(vals))
print("HEADER:", vals[0])
for r in vals[1:4]:
    print([c[:120] for c in r])

# date column detection
hdr = vals[0]
dates = Counter()
for r in vals[1:]:
    if len(r) > 0:
        dates[(r[0] or "")[:10]] += 1
print("=== counts by first-col date (top 12):")
for d, c in dates.most_common(12):
    print(d, c)

json.dump(vals, open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_orig.json", "w"), ensure_ascii=False)
print("saved")
