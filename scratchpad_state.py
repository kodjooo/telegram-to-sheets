import json, gspread
from oauth2client.service_account import ServiceAccountCredentials

BASE = "/Users/a1234/Documents/automation-projects/telegram-to-sheets/app/"
cfg = json.load(open(BASE + "config.json"))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(BASE + "google-credentials.json", scope)
gc = gspread.authorize(creds)
sh = gc.open_by_key(cfg["google_sheet_id"])
print("SHEET ID:", cfg["google_sheet_id"])

d = sh.worksheet("Digest")
print("=== DIGEST A1:", repr(d.acell("A1").value))
print("=== DIGEST A2 ===")
print(d.acell("A2").value)

u = sh.worksheet("Unknown tx")
uv = u.get_all_values()
print("=== UNKNOWN TX rows:", len(uv))
for r in uv[:40]:
    print(r)
