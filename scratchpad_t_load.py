import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app"
OUT = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_groups_full.json"
cfg = json.load(open(BASE + "/config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "/google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

print("SHEET_ID:", cfg["google_sheet_id"])
print("TABS:")
for ws in sh.worksheets():
    print("  ", ws.title, "gid=", ws.id, "rows=", ws.row_count)

g = sh.worksheet("Groups")
vals = g.get_all_values()
json.dump(vals, open(OUT, "w"), ensure_ascii=False)
print("GROUPS ROWS:", len(vals))
print("HEADER:", vals[0])

hdr = vals[0]
idx = {h: i for i, h in enumerate(hdr)}
print("IDX:", idx)

# counters
unproc = 0
for r in vals[1:]:
    st = r[7] if len(r) > 7 else ""
    if st.strip() != "обработано":
        unproc += 1
print("NOT PROCESSED TOTAL:", unproc)
