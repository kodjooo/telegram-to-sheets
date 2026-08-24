import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app"
cfg = json.load(open(BASE + "/config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "/google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])

d = sh.worksheet("Digest")
dv = d.get_all_values()
print("=== DIGEST A1:", dv[0][0] if dv and dv[0] else "(empty)")
print("=== DIGEST A2 (len %d):" % (len(dv[1][0]) if len(dv) > 1 and dv[1] else 0))
if len(dv) > 1 and dv[1]:
    print(dv[1][0])

u = sh.worksheet("Unknown tx")
uv = u.get_all_values()
print("\n=== UNKNOWN TX rows:", len(uv))
for r in uv[:40]:
    print("  ", r)

o = sh.worksheet("Original data")
ov = o.get_all_values()
json.dump(ov, open("/Users/a1234/Documents/automation-projects/telegram-to-sheets/scratchpad_orig.json", "w"), ensure_ascii=False)
print("\n=== ORIGINAL rows:", len(ov))
print("HEADER:", ov[0])
for r in ov[1:4]:
    print("  ", [c[:120] for c in r])
