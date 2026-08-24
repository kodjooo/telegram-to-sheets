import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app"
cfg = json.load(open(BASE + "/config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "/google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])
for ws in sh.worksheets():
    print(ws.title, "| gid=", ws.id, "| rows=", ws.row_count, "| cols=", ws.col_count)

g = sh.worksheet("Groups")
vals = g.get_all_values()
print("TOTAL ROWS", len(vals))
print("HEADER", vals[0])
out = open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups.json", "w")
json.dump(vals, out, ensure_ascii=False)
out.close()
