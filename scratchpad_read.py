import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

print("=== WORKSHEETS ===")
for ws in sh.worksheets():
    print(ws.title, "| gid=", ws.id, "| rows=", ws.row_count, "| cols=", ws.col_count)

g = sh.worksheet("Groups")
vals = g.get_all_values()
print("=== GROUPS rows:", len(vals))
print("HEADER:", vals[0])
out = []
for i, r in enumerate(vals[1:], start=2):
    out.append([i] + r)
json.dump(out, open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json", "w"), ensure_ascii=False)
print("saved groups")
