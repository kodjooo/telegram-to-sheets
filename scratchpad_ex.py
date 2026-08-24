import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

od = sh.worksheet("Original data")
vals = od.get_all_values()
print("ORIGINAL rows:", len(vals))
print("HEADER:", vals[0])
json.dump(vals, open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_orig.json", "w"), ensure_ascii=False)
for r in vals[1:4]:
    print([str(x)[:120] for x in r])
