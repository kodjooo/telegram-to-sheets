import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
OUT = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])
print("SHEET ID:", cfg["google_sheet_id"])

print("=== WORKSHEETS ===")
for ws in sh.worksheets():
    print(ws.title, "| gid=", ws.id, "| rows=", ws.row_count)

g = sh.worksheet("Groups")
vals = g.get_all_values()
print("=== GROUPS rows:", len(vals))
print("HEADER:", vals[0])
out = [[i] + r for i, r in enumerate(vals[1:], start=2)]
json.dump(out, open(OUT + "scratchpad_groups.json", "w"), ensure_ascii=False)

o = sh.worksheet("Original data")
ov = o.get_all_values()
print("=== ORIGINAL rows:", len(ov))
print("HEADER:", ov[0])
json.dump(ov, open(OUT + "scratchpad_orig.json", "w"), ensure_ascii=False)

d = sh.worksheet("Digest")
print("=== DIGEST A1:", repr(d.acell("A1").value))
print("=== DIGEST A2 ===")
print(d.acell("A2").value)

u = sh.worksheet("Unknown tx")
uv = u.get_all_values()
print("=== UNKNOWN TX rows:", len(uv))
for r in uv[:50]:
    print(r)
